//
//  ContentView.swift
//  Dawggles
//

import SwiftUI

struct ContentView: View {
    @StateObject private var connection = DawgglesConnection.shared
    @StateObject private var accessorySetup = DawgglesAccessorySetup.shared

    var body: some View {
        VStack(spacing: 30) {
            Image(systemName: "eyeglasses")
                .font(.system(size: 60))
                .foregroundColor(.blue)

            Text("Dawggles")
                .font(.largeTitle)
                .bold()

            VStack(spacing: 12) {
                Text("Bluetooth (Pi)")
                    .font(.caption)
                    .foregroundColor(.gray)
                    .frame(maxWidth: .infinity, alignment: .leading)

                HStack(spacing: 12) {
                    Button {
                        accessorySetup.startPairing()
                    } label: {
                        Text("Pair Dawggles")
                            .bold()
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                    }
                    .buttonStyle(.borderedProminent)

                    Button {
                        accessorySetup.unpairFromPhone()
                    } label: {
                        Text("Unpair")
                            .bold()
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                    }
                    .buttonStyle(.bordered)
                }

                if !accessorySetup.status.isEmpty {
                    Text(accessorySetup.status)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, 40)

            VStack(spacing: 12) {
                Text("WebSocket")
                    .font(.caption)
                    .foregroundColor(.gray)
                    .frame(maxWidth: .infinity, alignment: .leading)

                Button {
                    connection.connectWebSocket()
                } label: {
                    HStack {
                        Circle()
                            .fill(connection.isConnected ? Color.green : Color.red)
                            .frame(width: 10, height: 10)
                        Text(connection.isConnected ? "Connected" : "Connect")
                            .bold()
                            .frame(maxWidth: .infinity)
                    }
                    .padding(.vertical, 12)
                }
                .buttonStyle(.bordered)
            }
            .padding(.horizontal, 40)

            if let image = connection.receivedImage {
                VStack(spacing: 8) {
                    Text("Last Photo")
                        .font(.caption)
                        .foregroundColor(.gray)
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .cornerRadius(10)
                }
                .padding(.horizontal, 40)
            }

            Spacer()
        }
        .padding(.top, 50)
        .onAppear {
            accessorySetup.ensureSessionActivated()
        }
    }
}

struct ContentView_Previews: PreviewProvider {
    static var previews: some View {
        ContentView()
    }
}
