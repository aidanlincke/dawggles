//
//  ContentView.swift
//  Dawggles
//

import SwiftUI

struct ContentView: View {
    @StateObject private var connection = DawgglesConnection.shared
    @State private var password = ""
    
    var body: some View {
        VStack(spacing: 30) {
            Image(systemName: "eyeglasses")
                .font(.system(size: 60))
                .foregroundColor(.blue)
            
            Text("Dawggles")
                .font(.largeTitle)
                .bold()
            
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

#Preview {
    ContentView()
}
