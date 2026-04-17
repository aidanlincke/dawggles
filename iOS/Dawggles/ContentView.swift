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
                HStack(spacing: 6) {
                    Circle()
                        .fill(connection.isConnected ? Color.green : Color.red)
                        .frame(width: 8, height: 8)
                    Text(connection.isConnected ? "Connected" : "Disconnected")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 24)
            .padding(.top, 20)
            .padding(.bottom, 24)

            Divider()

            ScrollView {
                VStack(spacing: 24) {

                    // WebSocket
                    VStack(alignment: .leading, spacing: 10) {
                        Text("WebSocket")
                            .font(.caption)
                            .foregroundStyle(.secondary)

                        Button {
                            connection.connectWebSocket()
                        } label: {
                            Text(connection.isConnected ? "Reconnect" : "Connect")
                                .bold()
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 14)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(connection.isConnected)
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

                    // Accessory status
                    if !accessorySetup.status.isEmpty {
                        Text(accessorySetup.status)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .fixedSize(horizontal: false, vertical: true)
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
