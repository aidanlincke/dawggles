//
//  FocusAlignment.swift
//  Dawggles
//

import CoreGraphics
import UIKit
import Vision

struct TranslationGrouping {
    let index: Int
    let visionBox: CGRect
    let translatedText: String

    init?(dictionary: [String: Any], arrayIndex: Int) {
        guard let text = dictionary["translated_text"] as? String else { return nil }
        let x = (dictionary["x"] as? NSNumber)?.doubleValue ?? (dictionary["x"] as? Double) ?? 0
        let y = (dictionary["y"] as? NSNumber)?.doubleValue ?? (dictionary["y"] as? Double) ?? 0
        let w = (dictionary["w"] as? NSNumber)?.doubleValue ?? (dictionary["w"] as? Double) ?? 0
        let h = (dictionary["h"] as? NSNumber)?.doubleValue ?? (dictionary["h"] as? Double) ?? 0
        self.index = arrayIndex
        self.translatedText = text
        self.visionBox = CGRect(x: x, y: y, width: w, height: h)
    }
}

enum FocusAlignment {
    private static let workMaxSide: CGFloat = 320

    /// Index into `groupings` whose box contains the live-image center after aligning to `reference`, or `nil`.
    static func activeGroupingIndex(
        reference: UIImage,
        live: UIImage,
        groupings: [TranslationGrouping]
    ) -> Int? {
        guard !groupings.isEmpty,
              let refCG = scaledCGImage(reference, maxSide: workMaxSide),
              let liveCG = scaledCGImage(live, maxSide: workMaxSide) else {
            return nil
        }

        let rw = CGFloat(refCG.width)
        let rh = CGFloat(refCG.height)
        let lw = CGFloat(liveCG.width)
        let lh = CGFloat(liveCG.height)

        let request = VNTranslationalImageRegistrationRequest(targetedCGImage: liveCG, orientation: .up, options: [:])
        let handler = VNImageRequestHandler(cgImage: refCG, orientation: .up, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return nil
        }

        guard let obs = request.results?.first as? VNImageTranslationAlignmentObservation else {
            return nil
        }

        let pLive = CGPoint(x: 0.5 * lw, y: 0.5 * lh)
        let pRef = pLive.applying(obs.alignmentTransform)

        var hits: [(index: Int, area: CGFloat)] = []
        for g in groupings {
            let rect = visionPixelRect(normalized: g.visionBox, width: rw, height: rh)
            if rect.contains(pRef) {
                hits.append((g.index, rect.width * rect.height))
            }
        }

        return hits.min(by: { $0.area < $1.area })?.index
    }

    private static func visionPixelRect(normalized box: CGRect, width: CGFloat, height: CGFloat) -> CGRect {
        CGRect(
            x: box.origin.x * width,
            y: box.origin.y * height,
            width: box.width * width,
            height: box.height * height
        )
    }

    private static func scaledCGImage(_ image: UIImage, maxSide: CGFloat) -> CGImage? {
        let w = image.size.width
        let h = image.size.height
        guard w > 0, h > 0 else { return image.cgImage }
        let maxDim = max(w, h)
        let scale = min(1, maxSide / maxDim)
        guard scale < 1 - 0.001 else { return image.cgImage }

        let newW = w * scale
        let newH = h * scale
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: newW, height: newH))
        let scaled = renderer.image { _ in
            image.draw(in: CGRect(x: 0, y: 0, width: newW, height: newH))
        }
        return scaled.cgImage
    }
}
