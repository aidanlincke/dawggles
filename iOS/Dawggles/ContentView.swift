//
//  ContentView.swift
//  Dawggles
//

import SwiftUI

struct ContentView: View {
    @StateObject private var connection = DawgglesConnection.shared
    @StateObject private var accessorySetup = DawgglesAccessorySetup.shared
    @State private var password = ""
    
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

                Text(accessorySetup.status.isEmpty ? "Run pair.py on the Pi, then tap Pair Dawggles." : accessorySetup.status)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 40)
            
            VStack(alignment: .leading, spacing: 10) {
                Text("Pi Password:")
                    .font(.caption)
                    .foregroundColor(.gray)
                
                TextField("Enter DAWGGLES_PAIR_PASSWORD", text: $password)
                    .textFieldStyle(RoundedBorderTextFieldStyle())
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
            }
            .padding(.horizontal, 40)
            
            Button(action: {
                // Dismiss keyboard
                UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
                
                connection.connectToWiFi(password: password)
            }) {
                Text("Join Dawggles Wi-Fi")
                    .bold()
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.blue)
                    .foregroundColor(.white)
                    .cornerRadius(10)
            }
            .padding(.horizontal, 40)
            
            // Status Box
            VStack {
                Text("Status:")
                    .font(.caption)
                    .foregroundColor(.gray)
                
                Text(connection.status)
                    .font(.body)
                    .multilineTextAlignment(.center)
                    .padding()
            }
            .frame(maxWidth: .infinity)
            .background(Color.gray.opacity(0.1))
            .cornerRadius(10)
            .padding(.horizontal, 40)
            
            Spacer()
        }
        .padding(.top, 50)
    }
}

struct ContentView_Previews: PreviewProvider {
    static var previews: some View {
        ContentView()
    }
}
