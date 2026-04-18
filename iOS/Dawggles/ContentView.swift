//
//  ContentView.swift
//  Dawggles
//

import SwiftUI

// MARK: - Root

struct ContentView: View {
    @EnvironmentObject private var accessorySetup: DawgglesAccessorySetup

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
                // Connection status pill
                HStack(spacing: 6) {
                    Circle()
                        .fill(connection.isConnected ? Color.green : Color.red)
                        .frame(width: 8, height: 8)
                    Text(connection.isConnected ? "Connected" : "Disconnected")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                // Refresh button
                Button {
                    isRefreshing = true
                    accessorySetup.reconnect()
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(.secondary)
                        .rotationEffect(.degrees(isRefreshing ? 360 : 0))
                        .animation(
                            isRefreshing
                                ? .linear(duration: 0.7).repeatForever(autoreverses: false)
                                : .default,
                            value: isRefreshing
                        )
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

            Divider()

            ScrollView {
                VStack(spacing: 24) {
                    if let live = connection.previewImage {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Live preview")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Image(uiImage: live)
                                .resizable()
                                .scaledToFit()
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                            if let idx = liveAlignment.lastSentIndex {
                                Text("Active ROI: \(idx)")
                                    .font(.caption2)
                                    .foregroundStyle(.tertiary)
                            }
                        }
                    }
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
