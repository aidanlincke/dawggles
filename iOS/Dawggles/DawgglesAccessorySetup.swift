//
//  DawgglesAccessorySetup.swift
//  Dawggles
//
//  In-app BLE pairing / unpairing via AccessorySetupKit (system picker + bond removal).
//

import AccessorySetupKit
import Combine
import CoreBluetooth
import SwiftUI
import UIKit

/// Coordinates AccessorySetupKit for pairing with the Dawggles Pi over BLE without opening Settings.
@MainActor
final class DawgglesAccessorySetup: ObservableObject {
    static let shared = DawgglesAccessorySetup()

    /// Same custom service as `DawgglesConnection` / Pi firmware.
    static let dawgglesServiceUUID = CBUUID(string: "0000D100-0000-1000-8000-00805F9B34FB")

    // Hardcoded hotspot credentials — swap for dynamic values once the accessory advertises them.
    private static let hotspotSSID = "Dawggles"
    private static let hotspotPassword = "tamalgames"

    @Published var status: String = ""
    @Published private(set) var isSessionReady = false
    @Published private(set) var pairedAccessory: ASAccessory?

    private var session: ASAccessorySession?
    /// Same queue as Apple’s AccessorySetupKit samples; keeps callbacks off the SwiftUI main actor lock.
    private let sessionQueue = DispatchQueue.main

    private var didStartActivation = false
    private var pendingPickerAfterActivation = false

    private init() {}

    /// Starts the accessory session if needed. Events update `status` and `isSessionReady`.
    func ensureSessionActivated() {
        guard !didStartActivation else { return }
        guard let session = getOrCreateSession() else { return }
        didStartActivation = true

        session.activate(on: sessionQueue) { [weak self] event in
            guard let self else { return }
            self.handle(event: event)
        }
    }

    /// Run `pair.py` on the Pi first so the device is discoverable, then call this to show the system picker.
    func startPairing() {
        pendingPickerAfterActivation = true
        ensureSessionActivated()
        if isSessionReady {
            pendingPickerAfterActivation = false
            presentPicker()
        }
    }

    /// Removes the iOS BLE bond for the accessory managed through this app (still run `unpair.py` on the Pi).
    func unpairFromPhone() {
        ensureSessionActivated()
        guard let session = session else { return }
        guard isSessionReady else {
            status = "Setting up Bluetooth… try Unpair again in a moment."
            return
        }
        let match = session.accessories.first { accessory in
            accessory.displayName.localizedCaseInsensitiveContains("Dawggles")
        }
        guard let accessory = match ?? session.accessories.first else {
            status = "No accessory registered for this app. Tap Pair Dawggles first."
            return
        }
        status = "Removing accessory…"
        session.removeAccessory(accessory) { [weak self] error in
            Task { @MainActor in
                guard let self else { return }
                if let error {
                    self.status = "Unpair failed: \(error.localizedDescription)"
                } else {
                    self.status = "Removed from this iPhone. Run unpair.py on the Pi to clear the Pi side."
                }
            }
        }
    }

    // MARK: - Hotspot

    /// Joins the accessory's Wi-Fi hotspot via AccessorySetupKit.
    /// Credentials are hardcoded — replace once the accessory advertises them dynamically.
    func joinHotspot(accessory: ASAccessory) {
        guard let session else {
            status = "Session unavailable — cannot join hotspot."
            return
        }
        let options = ASAccessoryHotspotDescriptor(ssid: Self.hotspotSSID, passphrase: Self.hotspotPassword)
        session.joinAccessoryHotspot(accessory, options: options) { [weak self] error in
            Task { @MainActor in
                guard let self else { return }
                if let error {
                    self.status = "Hotspot join failed: \(error.localizedDescription)"
                } else {
                    self.status = "✅ Joined \(Self.hotspotSSID) Wi-Fi"
                }
            }
        }
    }

    // MARK: - Private

    private func handle(event: ASAccessoryEvent) {
        switch event.eventType {
        case .activated:
            isSessionReady = true
            if pendingPickerAfterActivation {
                pendingPickerAfterActivation = false
                presentPicker()
            }
        case .accessoryAdded:
            guard let accessory = event.accessory else { break }
            pairedAccessory = accessory
            let name = accessory.displayName
            status = "Added \(name). Joining hotspot…"
            joinHotspot(accessory: accessory)
        case .accessoryChanged:
            break
        case .accessoryRemoved:
            status = "Accessory removed from this app’s list."
        case .pickerDidPresent:
            status = "Choose Dawggles and confirm pairing if prompted."
        case .pickerDidDismiss:
            break
        case .pickerSetupPairing:
            status = "Pairing… if you see a code, confirm it matches the Pi."
        case .pickerSetupBridging:
            break
        case .pickerSetupRename:
            break
        case .pickerSetupFailed:
            if let error = event.error {
                status = "Setup failed: \(error.localizedDescription)"
            } else {
                status = "Setup failed."
            }
        case .accessoryDiscovered:
            break
        case .invalidated:
            isSessionReady = false
            didStartActivation = false
        case .migrationComplete:
            break
        case .unknown:
            break
        @unknown default:
            break
        }
    }

    private func presentPicker() {
        guard let session = session else {
            status = "Accessory session unavailable."
            return
        }
        var descriptor = ASDiscoveryDescriptor()
        descriptor.bluetoothServiceUUID = Self.dawgglesServiceUUID
        descriptor.bluetoothNameSubstring = "Dawggles"
        descriptor.supportedOptions = [.bluetoothPairingLE]
        descriptor.ssid = Self.hotspotSSID

        let symbol = UIImage(systemName: "eyeglasses") ?? UIImage()
        let item = ASPickerDisplayItem(
            name: "Dawggles",
            productImage: symbol,
            descriptor: descriptor
        )

        session.showPicker(for: [item]) { [weak self] error in
            Task { @MainActor in
                guard let self else { return }
                if let error {
                    self.status = "Could not show picker: \(error.localizedDescription)"
                }
            }
        }
    }

    private func getOrCreateSession() -> ASAccessorySession? {
#if targetEnvironment(simulator)
        status = "AccessorySetupKit pairing is only supported on physical iPhone/iPad."
        return nil
#else
        guard hasValidAccessorySetupConfiguration() else {
            status = "Accessory setup config is invalid. Check the app Info.plist values."
            return nil
        }
        if let session {
            return session
        }
        let created = ASAccessorySession()
        session = created
        return created
#endif
    }

    private func hasValidAccessorySetupConfiguration() -> Bool {
        let info = Bundle.main.infoDictionary ?? [:]

        let supports = (info["NSAccessorySetupKitSupports"] as? [String]) ?? (info["NSAccessorySetupSupports"] as? [String]) ?? []
        guard supports.contains("Bluetooth") else { return false }

        let names = (info["NSAccessorySetupBluetoothNames"] as? [String]) ?? []
        let services = (info["NSAccessorySetupBluetoothServices"] as? [String]) ?? []
        let companyIDs = (info["NSAccessorySetupBluetoothCompanyIdentifiers"] as? [String]) ?? []

        return !names.isEmpty && !services.isEmpty && !companyIDs.isEmpty
    }
}
