import Combine
import Foundation
import UIKit
import Vision

struct TranslationBlock {
    var text: String
    var boundingBox: CGRect
    var translatedText: String?
}

/// Runs Vision OCR on an image, groups the detected text into logical blocks,
/// then hands them off to the Translation framework via `translationTrigger`.
class ImageTranslator: ObservableObject {
    static let shared = ImageTranslator()

    @Published var blocksToTranslate: [TranslationBlock] = []
    @Published var translationTrigger = UUID()

    private var pendingCompletion: (([TranslationBlock]) -> Void)?

    private init() {}

    // MARK: - OCR + block grouping

    func processAndTranslate(image: UIImage, completion: @escaping ([TranslationBlock]) -> Void) {
        guard let cgImage = image.cgImage else {
            completion([])
            return
        }

        let request = VNRecognizeTextRequest { [weak self] request, error in
            guard let self else { return }

            guard let observations = request.results as? [VNRecognizedTextObservation], error == nil else {
                print("ImageTranslator: OCR failed — \(String(describing: error))")
                DispatchQueue.main.async { completion([]) }
                return
            }

            // Sort top-to-bottom (Vision Y=0 is bottom), then left-to-right within a row
            let sorted = observations.sorted {
                let dy = $0.boundingBox.minY - $1.boundingBox.minY
                return abs(dy) > 0.02 ? dy > 0 : $0.boundingBox.minX < $1.boundingBox.minX
            }

            // Merge nearby observations into paragraph blocks
            var blocks: [TranslationBlock] = []
            for obs in sorted {
                guard let top = obs.topCandidates(1).first else { continue }
                let box = obs.boundingBox
                let text = top.string

                if var last = blocks.last {
                    let yDist = last.boundingBox.minY - box.maxY
                    let xOverlap = min(last.boundingBox.maxX, box.maxX) - max(last.boundingBox.minX, box.minX)
                    let closeY = yDist > -0.05 && yDist < 0.05
                    let alignedX = xOverlap > 0 || abs(last.boundingBox.minX - box.minX) < 0.1

                    if closeY && alignedX {
                        last.text += " " + text
                        last.boundingBox = last.boundingBox.union(box)
                        blocks[blocks.count - 1] = last
                        continue
                    }
                }
                blocks.append(TranslationBlock(text: text, boundingBox: box))
            }

            guard !blocks.isEmpty else {
                print("ImageTranslator: No text found in image")
                DispatchQueue.main.async { completion([]) }
                return
            }

            DispatchQueue.main.async {
                self.pendingCompletion = completion
                self.blocksToTranslate = blocks
                self.translationTrigger = UUID()
            }
        }

        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.recognitionLanguages = ["zh-Hans", "zh-Hant", "ja", "ko", "en-US"]

        DispatchQueue.global(qos: .userInitiated).async {
            do {
                try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
            } catch {
                print("ImageTranslator: Failed to perform OCR — \(error)")
                DispatchQueue.main.async { completion([]) }
            }
        }
    }

    // MARK: - Called by TranslationViewModifier after translation completes

    func completeTranslation(translatedBlocks: [TranslationBlock]) {
        pendingCompletion?(translatedBlocks)
        pendingCompletion = nil
        blocksToTranslate = []
    }
}
