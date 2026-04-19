//
//  LiveAlignmentSession.swift
//  Dawggles
//
//  Live preview: throttled full-frame OCR, nearest-center focus, Vision **recognition_confidence** merge so
//  shaky frames don’t overwrite good text; send full Pi payload only when merged lines change, else `focus` only.
//

import Combine
import UIKit

private struct IndexHysteresis {
    let consecutiveTicksRequired: Int
    private var streakIndex: Int?
    private var streakCount = 0

    init(consecutiveTicksRequired: Int) {
        self.consecutiveTicksRequired = consecutiveTicksRequired
        self.streakIndex = nil
        self.streakCount = 0
    }

    mutating func reset() {
        streakIndex = nil
        streakCount = 0
    }

    /// `true` when `index` has won `consecutiveTicksRequired` ticks in a row.
    mutating func shouldCommit(_ index: Int) -> Bool {
        if index == streakIndex {
            streakCount += 1
        } else {
            streakIndex = index
            streakCount = 1
        }
        return streakCount >= consecutiveTicksRequired
    }
}

final class LiveAlignmentSession: ObservableObject {
    @Published private(set) var lastSentIndex: Int?
    /// `translated_text` for the grouping at `lastSentIndex`.
    @Published private(set) var lastSentROIText: String?
    /// Latest detected live OCR groupings (Vision-normalized coords). Use this for drawing live boxes so
    /// they appear/disappear with detection, independent of translation lag.
    @Published private(set) var liveDetectedGroupings: [[String: Any]] = []

    private weak var connection: DawgglesConnection?
    private var latestLiveFrame: UIImage?
    private var tick: AnyCancellable?
    private var hysteresis = IndexHysteresis(consecutiveTicksRequired: 4)
    private var lastCommittedIndex: Int?
    private var consecutiveAlignmentMisses = 0
    private let alignmentMissesBeforeHysteresisReset = 8
    private var consecutiveEmptyGroupings = 0
    private let emptyGroupingsClearThreshold = 3

    private var lastLiveOCRTime: CFAbsoluteTime = 0
    private let liveOCRMinInterval: CFTimeInterval = 0.28
    private var ocrSeq: Int = 0

    /// Running merge of OCR lines; prefers higher-confidence / stable readings per band index.
    private var committedGroupings: [[String: Any]]?
    /// Last snapshot actually sent to the Pi (for focus-only updates).
    private var lastPiFingerprint: String?
    private var lastPiActive: Int?
    /// Last **translated** groupings sent to the Pi (used for ROI label when only `focus` is sent).
    private var lastTranslatedGroupings: [[String: Any]]?
    private var liveTranslateSeq: Int = 0
    private var lastUIFingerprint: String?
    private var lastTranslationEnqueueWall: CFAbsoluteTime = 0
    private let translationMinInterval: CFTimeInterval = 0.85

    func disarm() {
        tick?.cancel()
        tick = nil
        connection = nil
        latestLiveFrame = nil
        hysteresis.reset()
        consecutiveAlignmentMisses = 0
        consecutiveEmptyGroupings = 0
        lastLiveOCRTime = 0
        ocrSeq = 0
        lastCommittedIndex = nil
        lastSentIndex = nil
        lastSentROIText = nil
        liveDetectedGroupings = []
        lastUIFingerprint = nil
        committedGroupings = nil
        lastPiFingerprint = nil
        lastPiActive = nil
        lastTranslatedGroupings = nil
        liveTranslateSeq = 0
        lastTranslationEnqueueWall = 0
    }

    /// Start live translation: OCR runs on preview frames only; requires Pi JPEG stream on `connection`.
    func arm(connection: DawgglesConnection) {
        disarm()
        self.connection = connection

        tick = Timer.publish(every: 1.0 / 10.0, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in
                self?.alignmentTick()
            }
    }

    func onLiveFrame(_ image: UIImage) {
        latestLiveFrame = image
    }

    private func alignmentTick() {
        guard let live = latestLiveFrame,
              let conn = connection,
              conn.isConnected else { return }

        let now = CFAbsoluteTimeGetCurrent()
        if now - lastLiveOCRTime < liveOCRMinInterval { return }
        lastLiveOCRTime = now

        let image = live
        ocrSeq += 1
        let seq = ocrSeq
        DispatchQueue.global(qos: .userInitiated).async {
            let g = LiveOCRGroupings.buildGroupings(from: image)
            DispatchQueue.main.async { [weak self] in
                guard let self, seq == self.ocrSeq else { return }
                self.handleLiveOCR(groupings: g, connection: conn)
            }
        }
    }

    private func handleLiveOCR(groupings raw: [[String: Any]], connection conn: DawgglesConnection) {
        if raw.isEmpty {
            consecutiveAlignmentMisses += 1
            consecutiveEmptyGroupings += 1
            if consecutiveAlignmentMisses >= alignmentMissesBeforeHysteresisReset {
                hysteresis.reset()
                consecutiveAlignmentMisses = 0
            }
            if consecutiveEmptyGroupings >= emptyGroupingsClearThreshold {
                // If we’re not detecting anything for a few ticks, clear boxes + selection so the UI reflects reality.
                liveDetectedGroupings = []
                lastSentIndex = nil
                lastSentROIText = nil
                committedGroupings = nil
                lastCommittedIndex = nil
                lastUIFingerprint = nil
            }
            return
        }
        consecutiveAlignmentMisses = 0
        consecutiveEmptyGroupings = 0

        let merged = Self.mergeCommittedWithNew(previous: committedGroupings, new: raw)
        committedGroupings = merged
        
        // Update live UI groupings (boxes) even if translation is pending.
        // Skip publishing if unchanged to avoid unnecessary SwiftUI redraws.
        let uiFingerprint = merged.compactMap {
            let x = ($0["x"] as? Double) ?? 0
            let y = ($0["y"] as? Double) ?? 0
            let w = ($0["w"] as? Double) ?? 0
            let h = ($0["h"] as? Double) ?? 0
            let t = ($0["translated_text"] as? String) ?? ""
            return "\(String(format: "%.3f", x)),\(String(format: "%.3f", y)),\(String(format: "%.3f", w)),\(String(format: "%.3f", h)):\(t)"
        }.joined(separator: "\u{1f}")
        if uiFingerprint != lastUIFingerprint {
            liveDetectedGroupings = merged
            lastUIFingerprint = uiFingerprint
        }

        let parsed = merged.enumerated().compactMap { i, d in TranslationGrouping(dictionary: d, arrayIndex: i) }
        guard !parsed.isEmpty else { return }

        let nearest = LiveOCRGroupings.indexOfGroupingNearestNormalizedCenter(merged)
        let clampedNearest = min(max(0, nearest), merged.count - 1)

        if lastCommittedIndex == nil {
            lastCommittedIndex = clampedNearest
            hysteresis.reset()
        } else if clampedNearest == lastCommittedIndex {
            hysteresis.reset()
        } else if hysteresis.shouldCommit(clampedNearest) {
            lastCommittedIndex = clampedNearest
            hysteresis.reset()
        }

        let active = lastCommittedIndex ?? clampedNearest
        let safeActive = min(max(0, active), merged.count - 1)

        let fingerprint = Self.groupingTextsFingerprint(merged)
        if fingerprint == lastPiFingerprint, safeActive == lastPiActive {
            return
        }

        if fingerprint == lastPiFingerprint, safeActive != lastPiActive {
            conn.sendActiveGroupingIndex(safeActive)
            lastPiActive = safeActive
            lastSentIndex = safeActive
            let label: String
            if let lg = lastTranslatedGroupings, safeActive >= 0, safeActive < lg.count {
                label = (lg[safeActive]["translated_text"] as? String) ?? ""
            } else {
                label = (merged[safeActive]["translated_text"] as? String) ?? ""
            }
            lastSentROIText = label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : label
            return
        }

        liveTranslateSeq += 1
        let seq = liveTranslateSeq
        let now = CFAbsoluteTimeGetCurrent()
        if now - lastTranslationEnqueueWall < translationMinInterval {
            // Don’t spam translations if OCR jitter causes frequent text changes.
            return
        }
        lastTranslationEnqueueWall = now
        ImageTranslator.shared.enqueueLiveGroupings(groupings: merged) { [weak self] translatedOut in
            DispatchQueue.main.async {
                guard let self else { return }
                guard seq == self.liveTranslateSeq else { return }
                guard let conn = self.connection, conn.isConnected else { return }

                let summary = translatedOut.compactMap { $0["translated_text"] as? String }.joined(separator: " ")
                conn.sendTranslationPayload(data: summary, groupings: translatedOut, activeIdx: safeActive)
                self.lastPiFingerprint = fingerprint
                self.lastPiActive = safeActive
                self.lastTranslatedGroupings = translatedOut
                self.lastSentIndex = safeActive
                let label = (translatedOut[safeActive]["translated_text"] as? String) ?? ""
                self.lastSentROIText = label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : label

                if let t = self.lastSentROIText, !t.isEmpty {
                    print("LiveAlignment: ROI \(safeActive) (nearest-center) — \(t)")
                } else {
                    print("LiveAlignment: ROI \(safeActive) (nearest-center) — (no text)")
                }
            }
        }
    }

    // MARK: - Confidence merge + similarity

    private static func recognitionConfidence(from d: [String: Any]) -> Double {
        if let n = d["recognition_confidence"] as? Double { return n }
        if let n = d["recognition_confidence"] as? Float { return Double(n) }
        if let n = d["recognition_confidence"] as? NSNumber { return n.doubleValue }
        return 1
    }

    /// When band count matches, per-index merge; otherwise take fresh OCR (layout changed).
    private static func mergeCommittedWithNew(previous: [[String: Any]]?, new: [[String: Any]]) -> [[String: Any]] {
        guard let prev = previous, prev.count == new.count, !new.isEmpty else { return new }
        return zip(prev, new).map { mergeGroupingDict(old: $0, new: $1) }
    }

    private static func mergeGroupingDict(old: [String: Any], new: [String: Any]) -> [String: Any] {
        let oT = (old["translated_text"] as? String) ?? ""
        let nT = (new["translated_text"] as? String) ?? ""
        let oC = recognitionConfidence(from: old)
        let nC = recognitionConfidence(from: new)
        if oT.isEmpty { return new }
        if nT.isEmpty { return old }
        if stringsSimilar(oT, nT) {
            return nC >= oC ? new : old
        }
        if nC >= oC + 0.12 {
            return new
        }
        return old
    }

    private static func normalizeForCompare(_ s: String) -> String {
        s.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
            .components(separatedBy: .whitespacesAndNewlines)
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }

    private static func stringsSimilar(_ a: String, _ b: String) -> Bool {
        let x = normalizeForCompare(a)
        let y = normalizeForCompare(b)
        if x.isEmpty || y.isEmpty { return false }
        if x == y { return true }
        if x.contains(y) || y.contains(x) { return true }
        let xs = Set(x.split(separator: " ").map(String.init))
        let ys = Set(y.split(separator: " ").map(String.init))
        let inter = xs.intersection(ys).count
        let uni = xs.union(ys).count
        guard uni > 0 else { return false }
        return Float(inter) / Float(uni) >= 0.55
    }

    private static func groupingTextsFingerprint(_ g: [[String: Any]]) -> String {
        g.map { ($0["translated_text"] as? String) ?? "" }.joined(separator: "\u{1e}")
    }
}
