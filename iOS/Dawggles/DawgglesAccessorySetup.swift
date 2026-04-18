//
//  DawgglesAccessorySetup.swift
//  Dawggles
//
//  In-app BLE pairing / unpairing via AccessorySetupKit (system picker + bond removal).
//

import AccessorySetupKit
import Combine
import CoreBluetooth
import NetworkExtension
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
    @Published private(set) var isPairing = false

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

    /// Show the system picker to pair a Dawggles device.
    func startPairing() {
        isPairing = true
        pendingPickerAfterActivation = true
        ensureSessionActivated()
        if isSessionReady {
            pendingPickerAfterActivation = false
            presentPicker()
        }
    }

    /// Presents the system removal confirmation. Only disconnects and leaves the AP if the user confirms.
    func unpairFromPhone() {
        guard isSessionReady, let session else { return }
        guard let accessory = session.accessories.first(where: {
            $0.displayName.localizedCaseInsensitiveContains("Dawggles")
        }) ?? session.accessories.first else { return }

        session.removeAccessory(accessory) { error in
            guard error == nil else { return }
            // User confirmed — now do the hard reset.
            // The accessoryRemoved event will fire separately and clear pairedAccessory.
            DawgglesConnection.shared.disconnect()
            NEHotspotConfigurationManager.shared.removeConfiguration(forSSID: Self.hotspotSSID)
        }
    }

    // MARK: - Hotspot

    /// Joins the accessory's Wi-Fi hotspot via NEHotspotConfigurationManager.
    /// Uses the accessory-linked API so the user isn't re-prompted — consent was already
    /// given when they approved the accessory in the AccessorySetupKit picker.
    /// Requires `NSAccessorySetupKitSupports` to include "WiFi" and the pairing
    /// descriptor to have had `ssid` set.
    func joinHotspot(accessory: ASAccessory) {
        NEHotspotConfigurationManager.shared.joinAccessoryHotspot(accessory,
                                                                  passphrase: Self.hotspotPassword) { error in
            Task { @MainActor in
                if let error {
                    let ns = error as NSError
                    let isAlreadyAssociated = ns.domain == NEHotspotConfigurationErrorDomain
                        && ns.code == NEHotspotConfigurationError.alreadyAssociated.rawValue
                    if !isAlreadyAssociated {
                        print("DawgglesAccessorySetup: joinAccessoryHotspot: \(error.localizedDescription)")
                    }
                }
                // Always attempt connection — the WebSocket retry loop handles cases
                // where DHCP hasn't finished yet or the join silently failed.
                Task {
                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                    DawgglesConnection.shared.connectWebSocket()
                }
            }
        }
    }

    /// Drops the WebSocket and re-joins the hotspot, then reconnects.
    func reconnect() {
        guard let accessory = pairedAccessory else { return }
        DawgglesConnection.shared.disconnect()
        joinHotspot(accessory: accessory)
    }

    // MARK: - Private

    private func handle(event: ASAccessoryEvent) {
        switch event.eventType {
        case .activated:
            isSessionReady = true
            if pendingPickerAfterActivation {
                pendingPickerAfterActivation = false
                presentPicker()
            } else if let accessory = session?.accessories.first(where: {
                $0.displayName.localizedCaseInsensitiveContains("Dawggles")
            }) ?? session?.accessories.first {
                pairedAccessory = accessory
                joinHotspot(accessory: accessory)
            }
        case .accessoryAdded:
            guard let accessory = event.accessory else { break }
            isPairing = false
            pairedAccessory = accessory
            joinHotspot(accessory: accessory)
        case .accessoryChanged:
            break
        case .accessoryRemoved:
            pairedAccessory = nil
            status = ""
        case .pickerDidPresent:
            status = "Choose Dawggles and confirm pairing if prompted."
        case .pickerDidDismiss:
            isPairing = false
        case .pickerSetupPairing:
            status = "Pairing… if you see a code, confirm it matches the Pi."
        case .pickerSetupBridging:
            break
        case .pickerSetupRename:
            break
        case .pickerSetupFailed:
            isPairing = false
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
            isPairing = false
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

