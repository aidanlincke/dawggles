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
    private static let hotspotIPv4Prefix = "192.168.4."
    private static let hotspotGateway = "192.168.4.1"
    private static let hotspotJoinRetryInterval: TimeInterval = 5.0
    private static let hotspotJoinTimeout: TimeInterval = 30.0

    @Published var status: String = ""
    @Published private(set) var isSessionReady = false
    @Published private(set) var pairedAccessory: ASAccessory?
    @Published private(set) var isPairing = false

    private var session: ASAccessorySession?
    /// Same queue as Apple’s AccessorySetupKit samples; keeps callbacks off the SwiftUI main actor lock.
    private let sessionQueue = DispatchQueue.main

    private var didStartActivation = false
    private var pendingPickerAfterActivation = false
    private var hotspotJoinRetryWorkItem: DispatchWorkItem?
    private var hotspotJoinStartedAt: Date?

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

        session.removeAccessory(accessory) { [weak self] error in
            guard error == nil else { return }
            // User confirmed — now do the hard reset.
            // The accessoryRemoved event will fire separately and clear pairedAccessory.
            self?.hotspotJoinRetryWorkItem?.cancel()
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
        hotspotJoinRetryWorkItem?.cancel()
        hotspotJoinStartedAt = Date()
        DawgglesConnection.shared.beginConnecting()
        joinHotspot(accessory: accessory, attempt: 1)
    }

    private func joinHotspot(accessory: ASAccessory, attempt: Int) {
        NEHotspotConfigurationManager.shared.joinAccessoryHotspot(accessory,
                                                                  passphrase: Self.hotspotPassword) { error in
            Task { @MainActor in
                if let error {
                    if self.isAlreadyAssociatedError(error) {
                        self.hotspotJoinRetryWorkItem?.cancel()
                        self.hotspotJoinStartedAt = nil
                        DawgglesConnection.shared.connectWebSocket(
                            expectedGateway: Self.hotspotGateway,
                            expectedIPv4Prefix: Self.hotspotIPv4Prefix
                        )
                        return
                    }

                    if self.shouldKeepRetryingHotspotJoin() {
                        let nextAttempt = attempt + 1
                        print("DawgglesAccessorySetup: joinAccessoryHotspot failed (attempt \(attempt)): \(error.localizedDescription)")

                        let work = DispatchWorkItem { [weak self] in
                            self?.joinHotspot(accessory: accessory, attempt: nextAttempt)
                        }
                        self.hotspotJoinRetryWorkItem = work
                        DispatchQueue.main.asyncAfter(deadline: .now() + Self.hotspotJoinRetryInterval, execute: work)
                        return
                    }

                    print("DawgglesAccessorySetup: joinAccessoryHotspot failed after timeout: \(error.localizedDescription)")
                    self.status = "Could not join Dawggles Wi-Fi. Please try again."
                    self.hotspotJoinRetryWorkItem?.cancel()
                    self.hotspotJoinStartedAt = nil
                    DawgglesConnection.shared.disconnect()
                    return
                }

                self.hotspotJoinRetryWorkItem?.cancel()
                self.hotspotJoinStartedAt = nil
                DawgglesConnection.shared.connectWebSocket(
                    expectedGateway: Self.hotspotGateway,
                    expectedIPv4Prefix: Self.hotspotIPv4Prefix
                )
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
            hotspotJoinRetryWorkItem?.cancel()
            hotspotJoinStartedAt = nil
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
            hotspotJoinRetryWorkItem?.cancel()
            hotspotJoinStartedAt = nil
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

    private func isAlreadyAssociatedError(_ error: Error) -> Bool {
        let ns = error as NSError
        return ns.domain == NEHotspotConfigurationErrorDomain
            && ns.code == NEHotspotConfigurationError.alreadyAssociated.rawValue
    }

    private func shouldKeepRetryingHotspotJoin() -> Bool {
        guard let startedAt = hotspotJoinStartedAt else { return false }
        return Date().timeIntervalSince(startedAt) < Self.hotspotJoinTimeout
    }
}

