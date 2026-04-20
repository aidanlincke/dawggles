//
//  LiveOCRGroupings.swift
//  Dawggles
//
//  Static Vision OCR helpers for live preview: band merge, confidence, Pi `groupings` dictionaries.
//

import UIKit
import Vision

enum LiveOCRGroupings {
    /// Vision top-candidate confidence in **[0, 1]** (not perfectly calibrated). Drops very weak boxes that often
    /// correspond to texture / “ghost” text. Raise (e.g. 0.55–0.6) if junk still appears; lower if real faint text is lost.
    private static let minimumObservationConfidence: Double = 0.5
    /// After band merge, drop a whole grouping whose **minimum** fragment confidence falls below this (typ. ≤ observation floor).
    private static let minimumMergedGroupingConfidence: Double = 0.45
    /// Live preview OCR can be expensive; downscale frames before Vision.
    private static let maxLiveOCRSide: CGFloat = 720

    /// OCR on-device; builds `groupings` for the Pi. `translated_text` holds source text until translation runs.
    /// Nearby lines (typical signs / packaging) are merged into one grouping with a union box and space-joined text.
    static func buildGroupings(from image: UIImage) -> [[String: Any]] {
        let scaled = scaleIfNeeded(image, maxSide: maxLiveOCRSide)
        guard let cgImage = scaled.cgImage else { return [] }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        // Reduces invented “words” from background texture vs language-correction on packaging/labels.
        request.usesLanguageCorrection = false
        request.recognitionLanguages = ["zh-Hans", "zh-Hant", "ja", "ko", "en-US"]
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

    // MARK: - Merge tight lines (signs)

    /// Vision boxes: normalized, origin **bottom-left**. Vertical span is `[y, y + h]`.
    private struct LineBox {
        var x: Double
        var y: Double
        var w: Double
        var h: Double
        var text: String
        /// `VNRecognizedText.confidence` for this fragment; band grouping uses **min** (conservative).
        var confidence: Double
    }

    /// 1) **Vertical bands** — group boxes with similar `midY` (stacked lines on a label).
    /// 2) **Horizontal runs** — within a band, split when the gap between consecutive boxes (reading order) is large
    ///    so side‑by‑side signs on the same row become separate groupings.
    /// 3) Each run: sort reading order, space‑join text, union bbox + min confidence.
    private static func mergeLinesReadingOrder(_ rows: [[String: Any]]) -> [[String: Any]] {
        var boxes: [LineBox] = []
        for d in rows {
            guard let text = d["translated_text"] as? String, !text.isEmpty else { continue }
            let x = doubleField(d["x"])
            let y = doubleField(d["y"])
            let w = doubleField(d["w"])
            let h = doubleField(d["h"])
            guard w > 0, h > 0 else { continue }
            let c = doubleField(d["recognition_confidence"])
            let conf = c > 0 ? c : 1
            boxes.append(LineBox(x: x, y: y, w: w, h: h, text: text, confidence: conf))
        }
        guard !boxes.isEmpty else { return [] }

        let bands = clusterIntoHorizontalBands(boxes)
        let ordered = bands.sorted { bandMeanMidY($0) > bandMeanMidY($1) }

        var groupings: [[String: Any]] = []
        var nextId = 0
        for band in ordered {
            let columnRuns = splitBandIntoHorizontalRuns(band)
            for run in columnRuns {
                let sortedRun = sortBoxesInBandReadingOrder(run)
                let g = unionGrouping(id: nextId, lines: sortedRun)
                if doubleField(g["recognition_confidence"]) >= minimumMergedGroupingConfidence {
                    groupings.append(g)
                    nextId += 1
                }
            }
        }
        return groupings.enumerated().map { i, d in
            var m = d
            m["id"] = i
            return m
        }
    }

    private static func midY(_ b: LineBox) -> Double { b.y + 0.5 * b.h }

    private static func bandMeanMidY(_ band: [LineBox]) -> Double {
        guard !band.isEmpty else { return 0 }
        return band.map { midY($0) }.reduce(0, +) / Double(band.count)
    }

    /// Group boxes whose vertical centers sit on the same **text line / band**; split when the gap between
    /// adjacent centers (in top-to-bottom sort order) exceeds a height-based threshold.
    private static func clusterIntoHorizontalBands(_ boxes: [LineBox]) -> [[LineBox]] {
        let hs = boxes.map(\.h).sorted()
        let hMed = hs[hs.count / 2]
        // Larger threshold → more lines stay in one vertical band (tighter multi-line blocks).
        let splitAt = max(0.034, 0.76 * hMed)

        let sorted = boxes.sorted { midY($0) > midY($1) }
        var bands: [[LineBox]] = [[sorted[0]]]
        for i in 1..<sorted.count {
            let prev = sorted[i - 1]
            let b = sorted[i]
            if midY(prev) - midY(b) > splitAt {
                bands.append([b])
            } else {
                bands[bands.count - 1].append(b)
            }
        }
        return bands
    }

    /// Within one vertical band, walk **reading order** and start a new grouping when the horizontal gap
    /// `(next.leading − prev.trailing)` exceeds a line-height–scaled threshold — separates adjacent signs on the same row.
    private static func splitBandIntoHorizontalRuns(_ band: [LineBox]) -> [[LineBox]] {
        let sorted = sortBoxesInBandReadingOrder(band)
        guard sorted.count > 1 else { return sorted.isEmpty ? [] : [sorted] }

        let hs = sorted.map(\.h).sorted()
        let hMed = hs[hs.count / 2]
        let gapSplit = max(0.036, 0.95 * hMed)

        var runs: [[LineBox]] = []
        var run: [LineBox] = [sorted[0]]
        for i in 1..<sorted.count {
            let prev = run.last!
            let next = sorted[i]
            let horizGap = next.x - (prev.x + prev.w)
            if horizGap > gapSplit {
                runs.append(run)
                run = [next]
            } else {
                run.append(next)
            }
        }
        runs.append(run)
        return runs
    }

    /// Vision: origin bottom-left — larger **midY** is higher on the image. Same-row uses a small midY tolerance, then **x**.
    private static func sortBoxesInBandReadingOrder(_ band: [LineBox]) -> [LineBox] {
        var boxes = band
        boxes.sort { a, b in
            let tol = max(0.007, 0.34 * min(a.h, b.h))
            let d = midY(a) - midY(b)
            if abs(d) > tol { return d > 0 }
            return a.x < b.x
        }
        return boxes
    }

    private static func unionGrouping(id: Int, lines: [LineBox]) -> [String: Any] {
        var minX = Double.greatestFiniteMagnitude
        var maxX = -Double.greatestFiniteMagnitude
        var minY = Double.greatestFiniteMagnitude
        var maxY = -Double.greatestFiniteMagnitude
        for L in lines {
            minX = min(minX, L.x)
            maxX = max(maxX, L.x + L.w)
            minY = min(minY, L.y)
            maxY = max(maxY, L.y + L.h)
        }
        let w = maxX - minX
        let h = maxY - minY
        let joined = lines.map(\.text).joined(separator: " ")
        let confMin = lines.map(\.confidence).min() ?? 1
        return [
            "id": id,
            "translated_text": joined,
            "x": minX,
            "y": minY,
            "w": w,
            "h": h,
            "recognition_confidence": confMin,
        ]
    }

    private static func doubleField(_ v: Any?) -> Double {
        if let n = v as? Double { return n }
        if let n = v as? Float { return Double(n) }
        if let n = v as? NSNumber { return n.doubleValue }
        if let n = v as? CGFloat { return Double(n) }
        return 0
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
