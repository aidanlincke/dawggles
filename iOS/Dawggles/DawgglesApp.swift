//
//  DawgglesApp.swift
//  Dawggles
//
//  Created by Aidan Lincke on 2/26/26.
//

import SwiftUI

@main
struct DawgglesApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(DawgglesConnection.shared)
                .environmentObject(DawgglesAccessorySetup.shared)
        }
    }
}
