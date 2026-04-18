//
//  LiveOCRFocus.swift
//  Dawggles
//
//  Throttled live OCR on a center crop + fuzzy string match to reference `groupings`.
//

import UIKit
import Vision

enum LiveOCRFocus {
    private static let maxOCRSide: CGFloat = 380
    /// Fraction of min(width,height) around the reticle to OCR.
    private static let centerCropFraction: CGFloat = 0.40

    /// Fast OCR on a downscaled image, center crop only. Returns joined text (reading order ~ top-to-bottom).
    static func recognizeFastCenterCrop(from image: UIImage) -> String {
        let scaled = scaleIfNeeded(image, maxSide: maxOCRSide)
        guard let cg = scaled.cgImage else { return "" }
        let w = CGFloat(cg.width)
        let h = CGFloat(cg.height)
        guard w > 2, h > 2 else { return "" }

        let side = max(32, min(w, h) * centerCropFraction)
        let rect = CGRect(
            x: floor((w - side) * 0.5),
            y: floor((h - side) * 0.5),
            width: floor(side),
            height: floor(side)
        ).intersection(CGRect(x: 0, y: 0, width: w, height: h))

        guard rect.width >= 16, rect.height >= 16,
              let cropped = cg.cropping(to: rect) else { return "" }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .fast
        request.usesLanguageCorrection = false

        let handler = VNImageRequestHandler(cgImage: cropped, orientation: .up, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return ""
        }

        guard let observations = request.results as? [VNRecognizedTextObservation], !observations.isEmpty else {
            return ""
        }

        let sorted = observations.sorted { a, b in
            let ta = a.boundingBox.origin.y + a.boundingBox.height
            let tb = b.boundingBox.origin.y + b.boundingBox.height
            if ta != tb { return ta > tb }
            return a.boundingBox.origin.x < b.boundingBox.origin.x
        }

        return sorted.compactMap { $0.topCandidates(1).first?.string }.joined(separator: " ")
    }

    /// Best grouping index for `liveText`, or `nil` if empty / below `minimumMatchScore`.
    static func bestGroupingIndex(
        liveText: String,
        groupings: [TranslationGrouping],
        stickyPreferredIndex: Int?,
        stickyScoreMargin: Float,
        minimumMatchScore: Float
    ) -> Int? {
        let trimmed = liveText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !groupings.isEmpty else { return nil }

        var scored: [(index: Int, score: Float, area: CGFloat)] = []
        for g in groupings {
            let s = textMatchScore(live: trimmed, group: g.translatedText)
            let area = g.visionBox.width * g.visionBox.height
            scored.append((g.index, s, CGFloat(area)))
        }

        scored.sort { a, b in
            if abs(a.score - b.score) > 0.0001 { return a.score > b.score }
            return a.area < b.area
        }

        let top = scored[0]
        if minimumMatchScore > 0, top.score < minimumMatchScore {
            return nil
        }

        if let sticky = stickyPreferredIndex,
           let stickyRow = scored.first(where: { $0.index == sticky }) {
            if top.index == sticky { return sticky }
            if top.score > stickyRow.score + stickyScoreMargin {
                return top.index
            }
            return sticky
        }

        return top.index
    }

    // MARK: - String match

    private static func normalize(_ s: String) -> String {
        s.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
            .components(separatedBy: .whitespacesAndNewlines)
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }

    /// Score in ~[0, 1]: higher means live snippet is more consistent with the reference line.
    private static func textMatchScore(live: String, group: String) -> Float {
        let l = normalize(live)
        let g = normalize(group)
        if l.isEmpty || g.isEmpty { return 0 }
        if l == g { return 1 }
        if g.contains(l) { return 0.92 }
        if l.contains(g) { return 0.88 }

        let lt = Set(l.split(separator: " ").map(String.init))
        let gt = Set(g.split(separator: " ").map(String.init))
        let inter = lt.intersection(gt).count
        let uni = lt.union(gt).count
        let jaccard: Float = uni > 0 ? Float(inter) / Float(uni) : 0

        let lc = Array(l)
        let gc = Array(g)
        var prefix = 0
        for i in 0..<min(lc.count, gc.count) where lc[i] == gc[i] {
            prefix += 1
        }
        let prefixBoost = Float(prefix) / Float(max(l.count, g.count)) * 0.25

        return min(1, jaccard + prefixBoost)
    }

    // MARK: - Image

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
