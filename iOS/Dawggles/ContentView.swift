//
//  ContentView.swift
//  Dawggles
//

import SwiftUI
import Translation

// MARK: - Root

struct ContentView: View {
    @EnvironmentObject private var accessorySetup: DawgglesAccessorySetup
    @StateObject private var translator = ImageTranslator.shared

    var body: some View {
        Group {
            if accessorySetup.pairedAccessory != nil {
                PairedView()
            } else {
                PairingView()
            }
        }
        .onAppear {
            accessorySetup.ensureSessionActivated()
        }
        .modifier(TranslationViewModifier(translator: translator))
    }
}

// MARK: - Translation modifier

private struct TranslationViewModifier: ViewModifier {
    @ObservedObject var translator: ImageTranslator
    @State private var configuration: TranslationSession.Configuration?

    func body(content: Content) -> some View {
        content
            .onChange(of: translator.translationTrigger) {
                if configuration == nil {
                    configuration = TranslationSession.Configuration()
                } else {
                    configuration?.invalidate()
                }
            }
            .translationTask(configuration) { session in
                let blocks = translator.blocksToTranslate
                guard !blocks.isEmpty else { return }

                var translated: [TranslationBlock] = []
                do {
                    for block in blocks {
                        let response = try await session.translate(block.text)
                        var b = block
                        b.translatedText = response.targetText
                        translated.append(b)
                    }
                    translator.completeTranslation(translatedBlocks: translated)
                } catch {
                    print("TranslationViewModifier: translation failed — \(error)")
                    translator.completeTranslation(translatedBlocks: blocks)
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

            Divider()

            ScrollView {
                VStack(spacing: 24) {
                    // Received image
                    if let image = connection.receivedImage {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Last Photo")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Image(uiImage: image)
                                .resizable()
                                .scaledToFit()
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
