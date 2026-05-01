//
//  LiveAlignmentSession.swift
//  Dawggles
//
//  Live preview: throttled full-frame OCR, nearest-center focus; send full Pi payload only when text changes.
//

import Combine
import Foundation
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

    private weak var connection: DawgglesConnection?
    private var latestLiveFrame: UIImage?
    private var tick: AnyCancellable?
    private var consecutiveAlignmentMisses = 0
    private let alignmentMissesBeforeHysteresisReset = 8
    private var consecutiveEmptyGroupings = 0
    private let emptyGroupingsClearThreshold = 3

    private var lastLiveOCRTime: CFAbsoluteTime = 0
    private let liveOCRMinInterval: CFTimeInterval = 0.28
    private var ocrSeq: Int = 0

    /// Last snapshot actually sent to the Pi.
    private var lastPiFingerprint: String?
    private var liveTranslateSeq: Int = 0
    private var lastUIFingerprint: String?
    private var lastTranslationEnqueueWall: CFAbsoluteTime = 0
    private let translationMinInterval: CFTimeInterval = 0.85
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
        ocrSeq += 1
        let seq = ocrSeq
        #if DEBUG
        print("[LIVE] LiveAlignment: OCR start seq=\(seq) img=\(Int(image.size.width))x\(Int(image.size.height))")
        #endif
        DispatchQueue.global(qos: .userInitiated).async {
            let g = LiveOCRGroupings.buildGroupings(from: image)
            DispatchQueue.main.async { [weak self] in
                guard let self, seq == self.ocrSeq else { return }
                #if DEBUG
                print("[LIVE] LiveAlignment: OCR done seq=\(seq) groupings=\(g.count)")
                #endif
                self.handleLiveOCR(groupings: g, connection: conn)
            }
        }
    }

    private func handleLiveOCR(groupings raw: [[String: Any]], connection conn: DawgglesConnection) {
        #if DEBUG
        // Per-tick logging removed to avoid console spam during streaming.
        #endif
        if raw.isEmpty {
            consecutiveAlignmentMisses += 1
            consecutiveEmptyGroupings += 1
            if consecutiveAlignmentMisses >= alignmentMissesBeforeHysteresisReset {
                consecutiveAlignmentMisses = 0
            }
            if consecutiveEmptyGroupings >= emptyGroupingsClearThreshold {
                // If we’re not detecting anything for a few ticks, clear boxes + selection so the UI reflects reality.
                liveDetectedGroupings = []
                lastSentIndex = nil
                lastSentROIText = nil
                lastUIFingerprint = nil
                #if DEBUG
                print("[LIVE] LiveAlignment: cleared live boxes (empty streak=\(consecutiveEmptyGroupings))")
                #endif
            }
            return
        }
        consecutiveAlignmentMisses = 0
        consecutiveEmptyGroupings = 0

        // Debug simplification: use raw Vision observations directly (no merge, no ROI selection).
        // For UI, attach `ui_text` when we already have a cached translation.
        let translator = Translator.shared
        let groupings: [[String: Any]] = raw.map { d in
            var m = d
            let src = (d["translated_text"] as? String) ?? ""
            if let cached = translator.cachedLiveTranslation(for: src),
               !cached.isEmpty,
               cached != src {
                m["ui_text"] = cached
            }
            return m
        }
        #if DEBUG
        if !groupings.isEmpty {
            let sample = groupings.prefix(4).compactMap { d -> String? in
                let t = (d["translated_text"] as? String) ?? ""
                let x = (d["x"] as? Double) ?? 0
                let y = (d["y"] as? Double) ?? 0
                let w = (d["w"] as? Double) ?? 0
                let h = (d["h"] as? Double) ?? 0
                let c = (d["recognition_confidence"] as? Double) ?? 0
                if t.isEmpty { return nil }
                return #"[#\(t.prefix(48))#] box=\#(String(format: "%.3f", x)),\#(String(format: "%.3f", y)),\#(String(format: "%.3f", w)),\#(String(format: "%.3f", h)) conf=\#(String(format: "%.2f", c))"#

            }
            if !sample.isEmpty {
                print("[LIVE] LiveAlignment: OCR sample: \(sample.joined(separator: " | "))")
            }
        }
        #endif
        
        // Update live UI groupings (boxes) even if translation is pending.
        // Skip publishing if unchanged to avoid unnecessary SwiftUI redraws.
        let uiFingerprint = groupings.compactMap {
            let x = ($0["x"] as? Double) ?? 0
            let y = ($0["y"] as? Double) ?? 0
            let w = ($0["w"] as? Double) ?? 0
            let h = ($0["h"] as? Double) ?? 0
            let t = ($0["translated_text"] as? String) ?? ""
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
        #if DEBUG
        print("[LIVE] LiveAlignment: textFingerprint len=\(fingerprint.count)")
        #endif
        if fingerprint == lastPiFingerprint {
            #if DEBUG
            print("[LIVE] LiveAlignment: skip (fingerprint == lastPiFingerprint)")
            #endif
            return
        }

        // Only enqueue translation if there are cache misses.
        let cacheMisses = groupings.reduce(into: 0) { acc, d in
            let src = (d["translated_text"] as? String) ?? ""
            if src.isEmpty { return }
            if translator.cachedLiveTranslation(for: src) == nil { acc += 1 }
        }
        if cacheMisses == 0 {
            lastPiFingerprint = fingerprint
            #if DEBUG
            print("[LIVE] LiveAlignment: no translation work (all cache hits)")
            #endif
            return
        }
        #if DEBUG
        print("[LIVE] LiveAlignment: translation needed cacheMisses=\(cacheMisses) rows=\(groupings.count)")
        #endif
        
        // If OCR text is effectively unchanged from the last translation request, don't enqueue again.
        if fingerprint == lastEnqueuedOCRFingerprint {
            #if DEBUG
            print("[LIVE] LiveAlignment: skip enqueue (OCR text unchanged from last request)")
            #endif
            return
        }

        liveTranslateSeq += 1
        let seq = liveTranslateSeq
        let now = CFAbsoluteTimeGetCurrent()
        if now - lastTranslationEnqueueWall < translationMinInterval {
            // Don’t spam translations if OCR jitter causes frequent text changes.
            #if DEBUG
            print(#"[LIVE] LiveAlignment: translation debounced (Δt=\(String(format: "%.2f", now - lastTranslationEnqueueWall))s)"#)
            #endif
            return
        }
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

                let summary = translatedOut.compactMap { $0["translated_text"] as? String }.joined(separator: " ")
                // No ROI/focus selection: Pi will default to index 0 if it wants to show a single line.
                conn.sendTranslationPayload(data: summary, groupings: translatedOut)
                self.lastPiFingerprint = fingerprint
                self.lastSentIndex = nil
                self.lastSentROIText = nil
                #if DEBUG
                print("[LIVE] LiveAlignment: translate complete seq=\(seq) rows=\(translatedOut.count)")
                #endif

            }
        }
    }

}
