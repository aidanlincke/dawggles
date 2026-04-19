//
//  ContentView.swift
//  Dawggles
//

import SwiftUI
import Translation
import Foundation
import Combine

// MARK: - Translation Settings

class TranslationSettings: ObservableObject {
    static let availableLanguages = ["Auto", "English", "Spanish", "Chinese", "French", "German", "Japanese", "Korean"]
    static let languageCodes = ["", "en", "es", "zh", "fr", "de", "ja", "ko"]
    
    @Published var selectedSourceIndex = 1 // English
    @Published var selectedTargetIndex = 2 // Spanish
    
    var sourceLanguage: Locale.Language? {
        selectedSourceIndex == 0 ? nil : Locale.Language(identifier: Self.languageCodes[selectedSourceIndex])
    }
    
    var targetLanguage: Locale.Language {
        Locale.Language(identifier: Self.languageCodes[selectedTargetIndex])
    }
}

// MARK: - Root

struct ContentView: View {
    @EnvironmentObject private var accessorySetup: DawgglesAccessorySetup
    @StateObject private var translator = ImageTranslator.shared
    @StateObject private var translationSettings = TranslationSettings()

    private var showPairedDashboard: Bool {
        #if DEBUG
        if MockPiTesting.isEnabled { return true }
        #endif
        return accessorySetup.pairedAccessory != nil
    }

    var body: some View {
        Group {
            if showPairedDashboard {
                PairedView()
            } else {
                PairingView()
            }
        }
        .environmentObject(translationSettings)
        .onAppear {
            accessorySetup.ensureSessionActivated()
            #if DEBUG
            if MockPiTesting.isEnabled {
                DawgglesConnection.shared.connectWebSocket()
            }
            #endif
        }
        .modifier(TranslationViewModifier(translator: translator, settings: translationSettings))
    }
}

// MARK: - Translation modifier

private struct TranslationViewModifier: ViewModifier {
    @ObservedObject var translator: ImageTranslator
    @ObservedObject var settings: TranslationSettings
    @State private var configuration: TranslationSession.Configuration?

    private func makeConfiguration() -> TranslationSession.Configuration {
        TranslationSession.Configuration(
            source: settings.sourceLanguage,
            target: settings.targetLanguage,
            preferredStrategy: .lowLatency
        )
    }

    func body(content: Content) -> some View {
        content
            .onChange(of: settings.selectedSourceIndex) {
                translator.clearLiveTranslationCache()
                let sourceCode = $0 == 0 ? "auto" : TranslationSettings.languageCodes[$0]
                print("TranslationViewModifier: source changed to \(sourceCode)")
                configuration = makeConfiguration()
            }
            .onChange(of: settings.selectedTargetIndex) {
                translator.clearLiveTranslationCache()
                let targetCode = TranslationSettings.languageCodes[$0]
                print("TranslationViewModifier: target changed to \(targetCode)")
                configuration = makeConfiguration()
            }
            .onChange(of: translator.translationTrigger) {
                if configuration == nil {
                    configuration = makeConfiguration()
                } else {
                    configuration?.invalidate()
                }
                let sourceCode = settings.selectedSourceIndex == 0 ? "auto" : TranslationSettings.languageCodes[settings.selectedSourceIndex]
                let targetCode = TranslationSettings.languageCodes[settings.selectedTargetIndex]
                print("TranslationViewModifier: starting translation with source=\(sourceCode) target=\(targetCode)")
            }
            .onChange(of: translator.liveTranslationTrigger) {
                if configuration == nil {
                    configuration = makeConfiguration()
                } else {
                    configuration?.invalidate()
                }
                let sourceCode = settings.selectedSourceIndex == 0 ? "auto" : TranslationSettings.languageCodes[settings.selectedSourceIndex]
                let targetCode = TranslationSettings.languageCodes[settings.selectedTargetIndex]
                print("TranslationViewModifier: starting live translation with source=\(sourceCode) target=\(targetCode)")
            }
            .translationTask(configuration) { session in
                let taskStart = CFAbsoluteTimeGetCurrent()
                let blocks = translator.blocksToTranslate
                print("TranslationViewModifier: blocksToTranslate count=\(blocks.count)")
                if !blocks.isEmpty {
                    var translated: [TranslationBlock] = []
                    do {
                        for block in blocks {
                            print("TranslationViewModifier: translating block text=\(block.text)")
                            let t0 = CFAbsoluteTimeGetCurrent()
                            let response = try await session.translate(block.text)
                            let dt = CFAbsoluteTimeGetCurrent() - t0
                            print("TranslationViewModifier: block translated=\(response.targetText)")
                            var b = block
                            b.translatedText = response.targetText
                            translated.append(b)
                        }
                        translator.completeTranslation(translatedBlocks: translated)
                    } catch {
                        let nsError = error as NSError
                        print("TranslationViewModifier: translation failed — \(error) domain=\(nsError.domain) code=\(nsError.code) desc=\(nsError.localizedDescription)")
                        let fallback = settings.selectedSourceIndex == 0 ? "Could not detect language" : "Translation unavailable"
                        let modifiedBlocks = blocks.map { var b = $0; b.translatedText = fallback; return b }
                        translator.completeTranslation(translatedBlocks: modifiedBlocks)
                    }
                    return
                }

                let live = translator.liveGroupingsToTranslate
                guard !live.isEmpty else {
                    print("TranslationViewModifier: no live groupings, marking complete")
                    translator.completeLiveTranslation(translatedGroupings: [])
                    return
                }

                var out: [[String: Any]] = []
                do {
                    var cacheHits = 0
                    var cacheMisses = 0
                    for row in live {
                        var m = row
                        let src = (m["translated_text"] as? String) ?? ""
                        if let cached = translator.cachedLiveTranslation(for: src), !cached.isEmpty {
                            m["translated_text"] = cached
                            cacheHits += 1
                        } else {
                            print("TranslationViewModifier: translating live source=\(src)")
                            let t0 = CFAbsoluteTimeGetCurrent()
                            let response = try await session.translate(src)
                            let dt = CFAbsoluteTimeGetCurrent() - t0
                            print("TranslationViewModifier: live translated=\(response.targetText)")
                            translator.storeLiveTranslation(response.targetText, for: src)
                            m["translated_text"] = response.targetText
                            cacheMisses += 1
                        }
                        out.append(m)
                    }
                    print("TranslationViewModifier: ✓ translation batch complete [#\(self.translator.triggerCount)]")
                    translator.completeLiveTranslation(translatedGroupings: out)
                } catch {
                    let nsError = error as NSError
                    print("TranslationViewModifier: ✗ live translation failed — \(error) domain=\(nsError.domain) code=\(nsError.code) desc=\(nsError.localizedDescription)")
                    let fallback = settings.selectedSourceIndex == 0 ? "Could not detect language" : "Translation unavailable"
                    let modifiedLive = live.map { var m = $0; m["translated_text"] = fallback; return m }
                    translator.completeLiveTranslation(translatedGroupings: modifiedLive)
                }
            }
    }
}

// MARK: - Pairing screen

private struct PairingView: View {
    @EnvironmentObject private var accessorySetup: DawgglesAccessorySetup

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 16) {
                Image(systemName: "eyeglasses")
                    .font(.system(size: 80))
                    .foregroundStyle(.blue)

                Text("Dawggles")
                    .font(.largeTitle)
                    .bold()

                Text("Pair your goggles to get started.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }

            Spacer()

            VStack(spacing: 14) {
                Button {
                    accessorySetup.startPairing()
                } label: {
                    Group {
                        if accessorySetup.isPairing {
                            HStack(spacing: 10) {
                                ProgressView()
                                    .tint(.white)
                                Text("Pairing…")
                                    .bold()
                            }
                        } else {
                            Text("Pair Dawggles")
                                .bold()
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                }
                .buttonStyle(.borderedProminent)
                .disabled(accessorySetup.isPairing)

                if !accessorySetup.status.isEmpty {
                    Text(accessorySetup.status)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, 40)
            .padding(.bottom, 60)
        }
    }
}

// MARK: - Paired / dashboard screen

private struct PairedView: View {
    @EnvironmentObject private var connection: DawgglesConnection
    @EnvironmentObject private var accessorySetup: DawgglesAccessorySetup
    
    @StateObject private var liveAlignment = LiveAlignmentSession()
    @ObservedObject private var translator = ImageTranslator.shared
    @EnvironmentObject var translationSettings: TranslationSettings
    @State private var isRefreshing = false
    
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            
            // Header
            HStack(spacing: 12) {
                Image(systemName: "eyeglasses")
                    .font(.system(size: 28))
                    .foregroundStyle(.blue)
                Text("Dawggles")
                    .font(.title2)
                    .bold()
                Spacer()
                // Connection pill
                Image(systemName: connection.isConnected ? "wifi" : "wifi.slash")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(connection.isConnected ? Color.green : Color.red)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Capsule().fill(connection.isConnected
                                               ? Color.green.opacity(0.12)
                                               : Color.red.opacity(0.12)))
                    .contentTransition(.symbolEffect(.replace))
                    .animation(.easeInOut(duration: 0.25), value: connection.isConnected)
                // Refresh button
                Button {
                    isRefreshing = true
                    accessorySetup.reconnect()
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 15, weight: .medium))
                        .foregroundStyle(.secondary)
                        .symbolEffect(.rotate, options: .repeating, isActive: isRefreshing)
                }
                .disabled(isRefreshing)
            }
            .padding(.horizontal, 24)
            .padding(.top, 20)
            .padding(.bottom, 24)
            .onChange(of: connection.isConnected) { _, connected in
                if connected { isRefreshing = false }
            }
            .onChange(of: connection.isConnecting) { _, connecting in
                // Stop the spinner if all retry attempts are exhausted
                if !connecting && !connection.isConnected { isRefreshing = false }
            }
            .onAppear {
                connection.liveAlignment = liveAlignment
            }
            .onDisappear {
                connection.liveAlignment = nil
                liveAlignment.disarm()
            }
            
            // Language selection
            HStack(spacing: 16) {
                VStack(alignment: .leading) {
                    Text("From")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Picker("From", selection: $translationSettings.selectedSourceIndex) {
                        ForEach(0..<TranslationSettings.availableLanguages.count, id: \.self) { index in
                            Text(TranslationSettings.availableLanguages[index])
                        }
                    }
                    .pickerStyle(.menu)
                }
                VStack(alignment: .leading) {
                    Text("To")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Picker("To", selection: $translationSettings.selectedTargetIndex) {
                        ForEach(1..<TranslationSettings.availableLanguages.count, id: \.self) { index in
                            Text(TranslationSettings.availableLanguages[index])
                        }
                    }
                    .pickerStyle(.menu)
                }
            }
            .padding(.horizontal, 24)
            .padding(.bottom, 16)
            
            Text("Active: \(TranslationSettings.availableLanguages[translationSettings.selectedSourceIndex]) → \(TranslationSettings.availableLanguages[translationSettings.selectedTargetIndex])")
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 24)
                .padding(.bottom, 12)
            
            Divider()
            
            ScrollView {
                VStack(spacing: 24) {
                    if let live = connection.previewImage {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Live preview")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            ZStack {
                                Image(uiImage: live)
                                    .resizable()
                                    .scaledToFit()
                                Canvas { context, size in
                                    // The preview image is `scaledToFit()` inside this ZStack, so the actual rendered
                                    // image rect may be letterboxed within `size`. Grouping boxes are normalized
                                    // Vision coordinates (origin bottom-left), so we need to:
                                    // 1) compute the rendered image rect, 2) flip Y into top-left UI space.
                                    let imgW = max(1, live.size.width)
                                    let imgH = max(1, live.size.height)
                                    let imgAspect = imgW / imgH
                                    let viewAspect = size.width / max(1, size.height)
                                    
                                    let drawW: CGFloat
                                    let drawH: CGFloat
                                    let offsetX: CGFloat
                                    let offsetY: CGFloat
                                    if imgAspect > viewAspect {
                                        drawW = size.width
                                        drawH = size.width / imgAspect
                                        offsetX = 0
                                        offsetY = (size.height - drawH) * 0.5
                                    } else {
                                        drawH = size.height
                                        drawW = size.height * imgAspect
                                        offsetY = 0
                                        offsetX = (size.width - drawW) * 0.5
                                    }
                                    
                                    func clamp01(_ v: Double) -> Double { min(1, max(0, v)) }
                                    func visionRectToViewRect(x: Double, y: Double, w: Double, h: Double) -> CGRect? {
                                        guard w > 0, h > 0 else { return nil }
                                        // Flip Y from Vision (bottom-left) into view (top-left).
                                        let vx = clamp01(x)
                                        let vw = clamp01(w)
                                        let vh = clamp01(h)
                                        let vyTop = clamp01(1.0 - y - h)
                                        
                                        let rx = offsetX + CGFloat(vx) * drawW
                                        let ry = offsetY + CGFloat(vyTop) * drawH
                                        let rw = CGFloat(vw) * drawW
                                        let rh = CGFloat(vh) * drawH
                                        guard rw >= 1, rh >= 1 else { return nil }
                                        return CGRect(x: rx, y: ry, width: rw, height: rh)
                                    }
                                    
                                    for (idx, grouping) in liveAlignment.liveDetectedGroupings.enumerated() {
                                        if let x = grouping["x"] as? Double,
                                           let y = grouping["y"] as? Double,
                                           let w = grouping["w"] as? Double,
                                           let h = grouping["h"] as? Double,
                                           let text = grouping["translated_text"] as? String {
                                            guard !text.isEmpty else { continue }
                                            guard let rect = visionRectToViewRect(x: x, y: y, w: w, h: h) else { continue }
                                            var path = Path(roundedRect: rect, cornerRadius: 4)
                                            context.stroke(path, with: .color(.blue), lineWidth: 2)
                                            
                                            // If we already have a translation for this box, draw it.
                                            if let ui = grouping["ui_text"] as? String, !ui.isEmpty {
                                                let anchor = CGPoint(x: rect.minX + 6, y: rect.minY + 6)
                                                context.draw(
                                                    Text(ui)
                                                        .font(.caption2)
                                                        .foregroundStyle(.white)
                                                        .padding(.horizontal, 6)
                                                        .padding(.vertical, 3)
                                                        .background(.black.opacity(0.75))
                                                        .clipShape(RoundedRectangle(cornerRadius: 6)),
                                                    at: anchor,
                                                    anchor: .topLeading
                                                )
                                            }
                                        }
                                    }
                                }
                            }
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                        }
                    }
                }
                .padding(24)
            }
            
            Divider()
            
            // Unpair — destructive, anchored to bottom
            Button(role: .destructive) {
                accessorySetup.unpairFromPhone()
            } label: {
                Text("Unpair Dawggles")
                    .font(.subheadline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 12)
        }
    }
}
