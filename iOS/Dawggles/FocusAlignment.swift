//
//  FocusAlignment.swift
//  Dawggles
//
//  OCR grouping model shared by still capture and live focus.
//

import CoreGraphics
import UIKit

struct TranslationGrouping {
    let index: Int
    let visionBox: CGRect
    let translatedText: String
    /// Vision `VNRecognizedText.confidence` (merged bands use the minimum fragment confidence).
    let recognitionConfidence: Double?

    init?(dictionary: [String: Any], arrayIndex: Int) {
        let text: String? = (dictionary["translated_text"] as? String)
            ?? (dictionary["translated_text"] as? NSString).map { $0 as String }
        guard let text, !text.isEmpty else { return nil }
        let x = Self.double(from: dictionary["x"])
        let y = Self.double(from: dictionary["y"])
        let w = Self.double(from: dictionary["w"])
        let h = Self.double(from: dictionary["h"])
        guard w > 0, h > 0 else { return nil }
        self.index = arrayIndex
        self.translatedText = text
        self.visionBox = CGRect(x: x, y: y, width: w, height: h)
        self.recognitionConfidence = Self.optionalDouble(from: dictionary["recognition_confidence"])
    }

    private static func double(from v: Any?) -> Double {
        if let n = v as? Double { return n }
        if let n = v as? Float { return Double(n) }
        if let n = v as? NSNumber { return n.doubleValue }
        if let n = v as? CGFloat { return Double(n) }
        return 0
    }

    private static func optionalDouble(from v: Any?) -> Double? {
        guard let v else { return nil }
        if v is NSNull { return nil }
        return double(from: v)
    }
}
