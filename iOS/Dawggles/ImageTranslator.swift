//
//  ImageTranslator.swift
//  Dawggles
//

import UIKit
import Vision

enum ImageTranslator {
    /// OCR on-device; builds `groupings` for the Pi. `translated_text` is the recognized string (translation can be layered on later).
    static func buildGroupings(from image: UIImage) -> [[String: Any]] {
        guard let cgImage = image.cgImage else { return [] }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true

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
            let text = candidate.string
            let b = obs.boundingBox
            rows.append([
                "id": i,
                "translated_text": text,
                "x": b.origin.x,
                "y": b.origin.y,
                "w": b.size.width,
                "h": b.size.height,
            ])
        }
        return rows
    }
}
