//
//  LiveOCRGroupings.swift
//  Dawggles
//
//  Static Vision OCR helpers for live preview: Pi `groupings` dictionaries.
//

import UIKit
import Vision

enum LiveOCRGroupings {
    /// Vision top-candidate confidence in **[0, 1]** (not perfectly calibrated). Drops very weak boxes that often
    /// correspond to texture / “ghost” text. Raise (e.g. 0.55–0.6) if junk still appears; lower if real faint text is lost.
    private static let minimumObservationConfidence: Double = 0.5
    /// Live preview OCR can be expensive; downscale frames before Vision.
    private static let maxLiveOCRSide: CGFloat = 720

    /// OCR on-device; builds `groupings` for the Pi. `translated_text` holds source text until translation runs.
    /// Nearby lines (typical signs / packaging) are merged into one grouping with a union box and space-joined text.
    /// Pass `sourceCode: ""` for Auto (all languages); otherwise only the selected language is searched.
    static func buildGroupings(from image: UIImage, sourceCode: String = "") -> [[String: Any]] {
        let scaled = scaleIfNeeded(image, maxSide: maxLiveOCRSide)
        guard let cgImage = scaled.cgImage else { return [] }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        // Reduces invented “words” from background texture vs language-correction on packaging/labels.
        request.usesLanguageCorrection = false
        request.recognitionLanguages = TranslationSettings.visionLanguages(for: sourceCode)
        let handler = VNImageRequestHandler(cgImage: cgImage, orientation: .up, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return []
        }

        guard let observations = request.results as? [VNRecognizedTextObservation] else { return [] }

        var rows: [[String: Any]] = []
        for (i, obs) in observations.enumerated() {
            guard let candidate = obs.topCandidates(1).first else { continue }
            let conf = Double(candidate.confidence)
            guard conf >= minimumObservationConfidence else { continue }
            let text = candidate.string
            // Skip purely numeric tokens — translating "1" → "1" clutters the display.
            guard !Self.isPurelyNumeric(text) else { continue }
            let b = obs.boundingBox
            // Store as Double so downstream `as? Double` / `TranslationGrouping` always sees numeric values
            // (plain CGFloat in [String: Any] often does not bridge to NSNumber).
            rows.append([
                "id": i,
                "translated_text": text,
                "x": Double(b.origin.x),
                "y": Double(b.origin.y),
                "w": Double(b.size.width),
                "h": Double(b.size.height),
                "recognition_confidence": conf,
            ])
        }
        
        // Debug simplification: do NOT merge/cluster—use Apple/Vision’s raw observations as-is.
        // Sort into approximate reading order (top-to-bottom, then left-to-right).
        rows.sort { a, b in
            let ay = doubleField(a["y"]) + doubleField(a["h"])
            let by = doubleField(b["y"]) + doubleField(b["h"])
            if abs(ay - by) > 0.015 { return ay > by }
            return doubleField(a["x"]) < doubleField(b["x"])
        }
        return rows
    }

    /// Vision-normalized space (origin bottom-left). Image center is `(0.5, 0.5)`.
    static func indexOfGroupingNearestNormalizedCenter(_ groupings: [[String: Any]]) -> Int {
        guard !groupings.isEmpty else { return 0 }
        if groupings.count == 1 { return 0 }
        let cx = 0.5
        let cy = 0.5
        var bestI = 0
        var bestD = Double.greatestFiniteMagnitude
        for (i, d) in groupings.enumerated() {
            let x = doubleField(d["x"])
            let y = doubleField(d["y"])
            let w = doubleField(d["w"])
            let h = doubleField(d["h"])
            guard w > 0, h > 0 else { continue }
            let gx = x + w * 0.5
            let gy = y + h * 0.5
            let dist = hypot(gx - cx, gy - cy)
            if dist < bestD {
                bestD = dist
                bestI = i
            }
        }
        return bestI
    }

    private static func doubleField(_ v: Any?) -> Double {
        if let n = v as? Double { return n }
        if let n = v as? Float { return Double(n) }
        if let n = v as? NSNumber { return n.doubleValue }
        if let n = v as? CGFloat { return Double(n) }
        return 0
    }
    
    /// Returns true when `text` consists entirely of digits and numeric punctuation
    /// (periods, commas, colons, slashes, hyphens, percent signs, spaces).
    /// Single digits, multi-digit numbers, times, and date fragments all match.
    private static func isPurelyNumeric(_ text: String) -> Bool {
        guard !text.isEmpty else { return false }
        let allowed = CharacterSet.decimalDigits
            .union(CharacterSet(charactersIn: " .,:/-%+"))
        return text.unicodeScalars.allSatisfy { allowed.contains($0) }
    }

    private static func scaleIfNeeded(_ image: UIImage, maxSide: CGFloat) -> UIImage {
        let w = image.size.width
        let h = image.size.height
        guard w > 0, h > 0 else { return image }
        let maxDim = max(w, h)
        let scale = min(1, maxSide / maxDim)
        guard scale < 1 - 0.001 else { return image }
        
        let newW = w * scale
        let newH = h * scale
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: newW, height: newH))
        return renderer.image { _ in
            image.draw(in: CGRect(x: 0, y: 0, width: newW, height: newH))
        }
    }
}
