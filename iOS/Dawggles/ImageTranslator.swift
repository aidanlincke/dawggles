//
//  ImageTranslator.swift
//  Dawggles
//

import Combine
import Foundation
import UIKit
import Vision
import CoreFoundation

struct TranslationBlock {
    var text: String
    var boundingBox: CGRect
    var translatedText: String?
}

/// Still-photo OCR + SwiftUI `translationTask` bridge; also enqueues live `groupings` for batch translation.
class ImageTranslator: ObservableObject {
    static let shared = ImageTranslator()

    @Published var blocksToTranslate: [TranslationBlock] = []
    @Published var translationTrigger = UUID()

    /// Live path: Pi dictionaries with source text in `translated_text` until `completeLiveTranslation`.
    @Published var liveGroupingsToTranslate: [[String: Any]] = []
    @Published var liveTranslationTrigger = UUID()
    @Published var liveTranslatedGroupings: [[String: Any]] = []
    @Published var triggerCount = 0
    @Published var isTranslating = false

    private var pendingCompletion: (([TranslationBlock]) -> Void)?
    private var pendingLiveGroupingsCompletion: (([[String: Any]]) -> Void)?
    private var queuedLiveGroupings: [[String: Any]]?
    private var queuedCompletion: (([[String: Any]]) -> Void)?

    private var liveTranslationStartWall: CFAbsoluteTime = 0
    private let translationStuckTimeout: CFTimeInterval = 10.0

    private let liveCacheQueue = DispatchQueue(label: "ImageTranslator.liveTranslationCache")
    private var liveTranslationCache: [String: String] = [:]

    private init() {}

    func clearLiveTranslationCache() {
        liveCacheQueue.async { [weak self] in
            self?.liveTranslationCache.removeAll()
        }
    }

    func cachedLiveTranslation(for key: String) -> String? {
        let k = Self.normalizeLiveCacheKey(key)
        return liveCacheQueue.sync { liveTranslationCache[k] }
    }

    func storeLiveTranslation(_ value: String, for key: String) {
        let k = Self.normalizeLiveCacheKey(key)
        liveCacheQueue.async { [weak self] in
            self?.liveTranslationCache[k] = value
        }
    }
    
    private static func normalizeLiveCacheKey(_ s: String) -> String {
        let folded = s.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
        let parts = folded.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }
        return parts.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: - Still photo: OCR + block grouping

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

    // MARK: - Live groupings (batch translate `translated_text` source strings)

    func enqueueLiveGroupings(groupings: [[String: Any]], completion: @escaping ([[String: Any]]) -> Void) {
        if isTranslating {
            let now = CFAbsoluteTimeGetCurrent()
            if liveTranslationStartWall > 0, now - liveTranslationStartWall > translationStuckTimeout {
                print("ImageTranslator: live translation watchdog tripped (>\(translationStuckTimeout)s); resetting")
                isTranslating = false
                pendingLiveGroupingsCompletion = nil
                queuedLiveGroupings = nil
                queuedCompletion = nil
            } else {
            print("ImageTranslator: translation in progress, queuing batch")
            queuedLiveGroupings = groupings
            queuedCompletion = completion
            return
            }
        }
        
        pendingLiveGroupingsCompletion = completion
        liveGroupingsToTranslate = groupings
        triggerCount += 1
        isTranslating = true
        liveTranslationStartWall = CFAbsoluteTimeGetCurrent()
        print("ImageTranslator: enqueueLiveGroupings #\(triggerCount) with \(groupings.count) items — STARTING TRANSLATION")
        liveTranslationTrigger = UUID()
    }

    /// Allows callers (e.g. Pi->phone JSON) to trigger a translation batch without a completion callback.
    /// The translated rows will still be published to `liveTranslatedGroupings` and `isTranslating` will be reset.
    func beginExternalLiveGroupingsTranslation(groupings: [[String: Any]]) {
        if isTranslating { return }
        pendingLiveGroupingsCompletion = nil
        liveGroupingsToTranslate = groupings
        triggerCount += 1
        isTranslating = true
        liveTranslationStartWall = CFAbsoluteTimeGetCurrent()
        print("ImageTranslator: beginExternalLiveGroupingsTranslation #\(triggerCount) with \(groupings.count) items")
        liveTranslationTrigger = UUID()
    }

    // MARK: - Called by TranslationViewModifier after translation completes

    func completeTranslation(translatedBlocks: [TranslationBlock]) {
        pendingCompletion?(translatedBlocks)
        pendingCompletion = nil
        blocksToTranslate = []
    }

    func completeLiveTranslation(translatedGroupings: [[String: Any]]) {
        print("ImageTranslator: completeLiveTranslation with \(translatedGroupings.count) items")
        isTranslating = false
        liveTranslationStartWall = 0
        pendingLiveGroupingsCompletion?(translatedGroupings)
        pendingLiveGroupingsCompletion = nil
        liveGroupingsToTranslate = []
        liveTranslatedGroupings = translatedGroupings
        
        // Process queued batch if any
        if let queued = queuedLiveGroupings, let queuedCb = queuedCompletion {
            queuedLiveGroupings = nil
            queuedCompletion = nil
            print("ImageTranslator: processing queued batch")
            enqueueLiveGroupings(groupings: queued, completion: queuedCb)
        }
    }
}
