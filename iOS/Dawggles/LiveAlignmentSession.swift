//
//  LiveAlignmentSession.swift
//  Dawggles
//
//  Live preview: throttled full-frame OCR, nearest-center focus; send full Pi payload only when text changes.
//

import Combine
import Foundation
import NaturalLanguage
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
    // ROI/focus selection removed for debugging/simplicity.
    @Published private(set) var lastSentIndex: Int? = nil
    @Published private(set) var lastSentROIText: String? = nil
    /// Latest detected live OCR groupings (Vision-normalized coords). Use this for drawing live boxes so
    /// they appear/disappear with detection, independent of translation lag.
    @Published private(set) var liveDetectedGroupings: [[String: Any]] = []

    /// Source language code (from TranslationSettings) — drives which Vision languages are searched.
    /// `""` = Auto (all languages). Updated by ContentView on language change.
    var sourceCode: String = ""

    private weak var connection: DawgglesConnection?
    private var latestLiveFrame: UIImage?
    private var tick: AnyCancellable?
    private var consecutiveAlignmentMisses = 0
    private let alignmentMissesBeforeHysteresisReset = 8
    private var consecutiveEmptyGroupings = 0
    private let emptyGroupingsClearThreshold = 3
    // After this many consecutive empty OCR ticks (~1.7 s) the Pi display is cleared.
    // Higher than emptyGroupingsClearThreshold so momentary OCR drops don't flicker the glasses.
    private let piClearThreshold = 6

    // Removal debounce: a Pi update that only removes blocks (strict subset of what's on screen)
    // must be stable for this many consecutive OCR ticks (~1.1 s) before being committed.
    private var lastPiGroupingTexts: Set<String> = []
    private var pendingRemovalFingerprint: String? = nil
    private var pendingRemovalTicks = 0
    private let removalDebounceTicks = 4

    private var lastLiveOCRTime: CFAbsoluteTime = 0
    private let liveOCRMinInterval: CFTimeInterval = 0.28
    private var ocrSeq: Int = 0

    /// Last snapshot actually sent to the Pi.
    private var lastPiFingerprint: String?
    private var liveTranslateSeq: Int = 0
    private var lastUIFingerprint: String?
    private var lastTranslationEnqueueWall: CFAbsoluteTime = 0
    private let translationMinInterval: CFTimeInterval = 0.4
    private var lastEnqueuedOCRFingerprint: String?
    private var lastDumpWall: CFAbsoluteTime = 0
    private let dumpMinInterval: CFTimeInterval = 1.5

    private func dumpImageIfNeeded(_ image: UIImage?, tag: String) {
        #if DEBUG
        guard let image else { return }
        let now = CFAbsoluteTimeGetCurrent()
        guard now - lastDumpWall >= dumpMinInterval else { return }
        lastDumpWall = now

        guard let data = image.jpegData(compressionQuality: 0.85) else { return }
        let dir = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        let name = "live_\(Int(now * 1000))_\(tag).jpg"
        let url = dir.appendingPathComponent(name)
        do {
            try data.write(to: url, options: [.atomic])
            print("[LIVE] LiveAlignment: dumped OCR frame -> \(url.path) (\(data.count) bytes)")
        } catch {
            print("[LIVE] LiveAlignment: failed to dump OCR frame: \(error)")
        }
        #endif
    }

    func disarm() {
        #if DEBUG
        print("[LIVE] LiveAlignment: disarm()")
        #endif
        tick?.cancel()
        tick = nil
        connection = nil
        latestLiveFrame = nil
        consecutiveAlignmentMisses = 0
        consecutiveEmptyGroupings = 0
        lastLiveOCRTime = 0
        ocrSeq = 0
        lastSentIndex = nil
        lastSentROIText = nil
        liveDetectedGroupings = []
        lastUIFingerprint = nil
        lastPiFingerprint = nil
        liveTranslateSeq = 0
        lastTranslationEnqueueWall = 0
        lastPiGroupingTexts = []
        pendingRemovalFingerprint = nil
        pendingRemovalTicks = 0
    }

    func arm(connection: DawgglesConnection) {
        guard tick == nil else { return }
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
        let capturedSourceCode = sourceCode
        ocrSeq += 1
        let seq = ocrSeq
        #if DEBUG
        print("[LIVE] LiveAlignment: OCR start seq=\(seq) img=\(Int(image.size.width))x\(Int(image.size.height))")
        #endif
        DispatchQueue.global(qos: .userInitiated).async {
            let g = LiveOCRGroupings.buildGroupings(from: image, sourceCode: capturedSourceCode)
            DispatchQueue.main.async { [weak self] in
                guard let self, seq == self.ocrSeq else { return }
                #if DEBUG
                print("[LIVE] LiveAlignment: OCR done seq=\(seq) groupings=\(g.count)")
                #endif
                self.handleLiveOCR(groupings: g, connection: conn)
            }
        }
    }



    /// Returns true when `text` is predominantly in the language identified by `code`
    /// (e.g. "zh" matches "zh-Hans"/"zh-Hant", "en" matches "en", etc.).
    /// Uncertain/empty text passes through to avoid over-filtering short labels.
    private func textMatchesLanguage(_ text: String, code: String) -> Bool {
        let recognizer = NLLanguageRecognizer()
        recognizer.processString(text)
        guard let dominant = recognizer.dominantLanguage else { return true }
        return dominant.rawValue.hasPrefix(code)
    }

    private func handleLiveOCR(groupings raw: [[String: Any]], connection conn: DawgglesConnection) {
        #if DEBUG
        // Per-tick logging removed to avoid console spam during streaming.
        #endif

        // If a specific source language is selected, drop any OCR block whose text is not
        // in that language (e.g. skip English boxes when translating Chinese → English).
        let filtered: [[String: Any]]
        if let settings = conn.translationSettings, settings.selectedSourceIndex != 0 {
            let sourceCode = TranslationSettings.sourceLanguageCodes[settings.selectedSourceIndex]
            filtered = raw.filter { block in
                guard let text = block["translated_text"] as? String, !text.isEmpty else { return false }
                return textMatchesLanguage(text, code: sourceCode)
            }
            #if DEBUG
            if filtered.count != raw.count {
                print("[LIVE] LiveAlignment: language filter src=\(sourceCode) kept=\(filtered.count)/\(raw.count)")
            }
            #endif
        } else {
            filtered = raw
        }

        if filtered.isEmpty {
            consecutiveAlignmentMisses += 1
            consecutiveEmptyGroupings += 1
            if consecutiveAlignmentMisses >= alignmentMissesBeforeHysteresisReset {
                consecutiveAlignmentMisses = 0
            }
            if consecutiveEmptyGroupings >= emptyGroupingsClearThreshold {
                liveDetectedGroupings = []
                lastSentIndex = nil
                lastSentROIText = nil
                lastUIFingerprint = nil
                #if DEBUG
                print("[LIVE] LiveAlignment: cleared live boxes (empty streak=\(consecutiveEmptyGroupings))")
                #endif
            }
            if consecutiveEmptyGroupings == piClearThreshold {
                conn.sendTranslationPayload(data: "", groupings: [])
                lastPiFingerprint = nil
                lastEnqueuedOCRFingerprint = nil
                lastPiGroupingTexts = []
                pendingRemovalFingerprint = nil
                pendingRemovalTicks = 0
                #if DEBUG
                print("[LIVE] LiveAlignment: sent Pi clear (empty streak=\(consecutiveEmptyGroupings))")
                #endif
            }
            return
        }
        consecutiveAlignmentMisses = 0
        consecutiveEmptyGroupings = 0

        let translator = Translator.shared
        let groupings: [[String: Any]] = filtered.map { d in
            var m = d
            let src = (d["translated_text"] as? String) ?? ""
            if let cached = translator.cachedLiveTranslation(for: src), !cached.isEmpty, cached != src {
                m["ui_text"] = cached
            }
            return m
        }

        // Update iPhone UI overlay boxes.
        let uiFingerprint = groupings.compactMap { d -> String? in
            let x = (d["x"] as? Double) ?? 0
            let y = (d["y"] as? Double) ?? 0
            let w = (d["w"] as? Double) ?? 0
            let h = (d["h"] as? Double) ?? 0
            let t = (d["translated_text"] as? String) ?? ""
            return "\(String(format: "%.3f", x)),\(String(format: "%.3f", y)),\(String(format: "%.3f", w)),\(String(format: "%.3f", h)):\(t)"
        }.joined(separator: "\u{1f}")
        if uiFingerprint != lastUIFingerprint {
            liveDetectedGroupings = groupings
            lastUIFingerprint = uiFingerprint
            #if DEBUG
            print("[LIVE] LiveAlignment: UI boxes updated count=\(groupings.count)")
            #endif
        }

        func normalizeForFingerprint(_ s: String) -> String {
            let folded = s.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
            let parts = folded.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }
            return parts.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
        }
        let fingerprint = groupings
            .map { normalizeForFingerprint(($0["translated_text"] as? String) ?? "") }
            .joined(separator: "\u{1e}")
        if fingerprint == lastPiFingerprint { return }

        // Debounce pure removals: if the new set is a strict subset of what is already on the Pi
        // (blocks dropped, nothing new), require it to be stable for removalDebounceTicks before
        // sending. New text appearing bypasses the debounce entirely.
        let currentTexts = Set(groupings.compactMap { $0["translated_text"] as? String }
            .map { normalizeForFingerprint($0) }.filter { !$0.isEmpty })
        let isPureRemoval = !lastPiGroupingTexts.isEmpty && currentTexts.isSubset(of: lastPiGroupingTexts)
        if isPureRemoval {
            if fingerprint == pendingRemovalFingerprint {
                pendingRemovalTicks += 1
            } else {
                pendingRemovalFingerprint = fingerprint
                pendingRemovalTicks = 1
            }
            guard pendingRemovalTicks >= removalDebounceTicks else { return }
            pendingRemovalFingerprint = nil
            pendingRemovalTicks = 0
        } else {
            pendingRemovalFingerprint = nil
            pendingRemovalTicks = 0
        }

        let cacheMisses = groupings.reduce(into: 0) { acc, d in
            let src = (d["translated_text"] as? String) ?? ""
            if src.isEmpty { return }
            if translator.cachedLiveTranslation(for: src) == nil { acc += 1 }
        }
        if cacheMisses == 0 {
            let translatedGroupings: [[String: Any]] = groupings.map { d in
                var m = d
                let src = (d["translated_text"] as? String) ?? ""
                if let cached = translator.cachedLiveTranslation(for: src), !cached.isEmpty {
                    m["translated_text"] = cached
                }
                return m
            }
            let summary = translatedGroupings.compactMap { $0["translated_text"] as? String }.joined(separator: "\n")
            conn.sendTranslationPayload(data: summary, groupings: translatedGroupings)
            lastPiFingerprint = fingerprint
            lastPiGroupingTexts = currentTexts
            #if DEBUG
            print("[LIVE] LiveAlignment: all cache hits — sent direct to Pi rows=\(translatedGroupings.count)")
            #endif
            return
        }

        if fingerprint == lastEnqueuedOCRFingerprint { return }

        liveTranslateSeq += 1
        let seq = liveTranslateSeq
        let now = CFAbsoluteTimeGetCurrent()
        if now - lastTranslationEnqueueWall < translationMinInterval { return }
        lastTranslationEnqueueWall = now
        lastEnqueuedOCRFingerprint = fingerprint
        #if DEBUG
        print("[LIVE] LiveAlignment: enqueue translate seq=\(seq) rows=\(groupings.count)")
        #endif
        dumpImageIfNeeded(latestLiveFrame, tag: "enqueue_seq_\(seq)")
        Translator.shared.enqueueLiveGroupings(groupings: groupings) { [weak self] translatedOut in
            DispatchQueue.main.async {
                guard let self else { return }
                guard seq == self.liveTranslateSeq else { return }
                guard let conn = self.connection, conn.isConnected else { return }

                let summary = translatedOut.compactMap { $0["translated_text"] as? String }.joined(separator: "\n")
                conn.sendTranslationPayload(data: summary, groupings: translatedOut)
                self.lastPiFingerprint = fingerprint
                self.lastPiGroupingTexts = currentTexts
                self.lastSentIndex = nil
                self.lastSentROIText = nil
                #if DEBUG
                print("[LIVE] LiveAlignment: translate complete seq=\(seq) rows=\(translatedOut.count)")
                #endif
            }
        }
    }

}
