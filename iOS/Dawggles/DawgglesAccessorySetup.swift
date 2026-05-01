//
//  DawgglesAccessorySetup.swift
//  Dawggles
//
//  In-app BLE pairing / unpairing via AccessorySetupKit (system picker + bond removal).
//
//  After ASK pairing completes the app opens a CBCentralManager connection back to
//  the Pi and reads the token characteristic (UUID D101).  The Pi serves a 64-byte
//  payload:  token[0:32] || SHA-256(TLS cert DER)[32:64].  Both values are stored in
//  the Keychain before the app proceeds to join the Wi-Fi hotspot, giving
//  DawgglesConnection the material it needs for WSS cert-pinning and bearer-token auth.
//

import AccessorySetupKit
import Combine
import CoreBluetooth
import CryptoKit
import NetworkExtension
import SwiftUI
import UIKit

/// Coordinates AccessorySetupKit for pairing with the Dawggles Pi over BLE without opening Settings.
@MainActor
final class DawgglesAccessorySetup: NSObject, ObservableObject {
    static let shared = DawgglesAccessorySetup()

    /// Primary service UUID — used by both ASK discovery and GATT service discovery.
    static let dawgglesServiceUUID  = CBUUID(string: "0000D100-0000-1000-8000-00805F9B34FB")
    /// Characteristic UUID that carries the 64-byte (token || cert fingerprint) payload.
    static let tokenCharUUID        = CBUUID(string: "0000D101-0000-1000-8000-00805F9B34FB")

    // Hotspot credentials — SSID/password are static; gateway is inferred at connection time.
    private static let hotspotSSID              = "Dawggles"
    private static let hotspotPassword          = "tamalgames"
    private static let hotspotIPv4Prefix        = "192.168.4."
    private static let hotspotGateway           = "192.168.4.1"
    private static let hotspotJoinRetryInterval: TimeInterval = 5.0
    private static let hotspotJoinTimeout:       TimeInterval = 30.0

    @Published var status: String = ""
    @Published private(set) var isSessionReady   = false
    @Published private(set) var pairedAccessory: ASAccessory?
    @Published private(set) var isPairing        = false

    private var session: ASAccessorySession?
    private let sessionQueue = DispatchQueue.main

    private var didStartActivation              = false
    private var pendingPickerAfterActivation    = false
    private var hotspotJoinRetryWorkItem: DispatchWorkItem?
    private var hotspotJoinStartedAt: Date?

    // ── BLE credential fetch (post-pairing GATT read) ─────────────────────────
    private var centralManager: CBCentralManager?
    private var tokenPeripheral: CBPeripheral?
    private var tokenFetchCompletion: (() -> Void)?
    private var tokenFetchTimer: Timer?
    /// How long to wait for the GATT read before falling back to hotspot join.
    private static let tokenFetchTimeout: TimeInterval = 30.0

    private override init() {}

    // MARK: - Session lifecycle

    func ensureSessionActivated() {
        guard !didStartActivation else { return }
        guard let session = getOrCreateSession() else { return }
        didStartActivation = true

        session.activate(on: sessionQueue) { [weak self] event in
            guard let self else { return }
            self.handle(event: event)
        }
    }

    func startPairing() {
        isPairing = true
        pendingPickerAfterActivation = true
        ensureSessionActivated()
        if isSessionReady {
            pendingPickerAfterActivation = false
            presentPicker()
        }
    }

    func unpairFromPhone() {
        guard isSessionReady, let session else { return }
        guard let accessory = session.accessories.first(where: {
            $0.displayName.localizedCaseInsensitiveContains("Dawggles")
        }) ?? session.accessories.first else { return }

        session.removeAccessory(accessory) { [weak self] error in
            guard error == nil else { return }
            self?.hotspotJoinRetryWorkItem?.cancel()
            DawgglesConnection.shared.disconnect()
            NEHotspotConfigurationManager.shared.removeConfiguration(forSSID: Self.hotspotSSID)
        }
    }

    // MARK: - Hotspot

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

    func reconnect() {
        guard let accessory = pairedAccessory else { return }
        DawgglesConnection.shared.disconnect()
        joinHotspot(accessory: accessory)
    }

    // MARK: - Post-pairing credential fetch via BLE GATT

    /// After ASK confirms pairing, read the 64-byte credential characteristic from the Pi
    /// before proceeding to join the hotspot.  The Pi keeps the GATT service up for 30 s
    /// after pairing completes, giving us plenty of time to connect and read.
    private func fetchTokenViaBLE(then completion: @escaping () -> Void) {
        status = "Securing connection..."
        tokenFetchCompletion = completion

        // Timeout: if GATT read takes too long, proceed anyway (WebSocket will fail auth
        // and the user will be prompted to re-pair — better than hanging forever).
        tokenFetchTimer = Timer.scheduledTimer(withTimeInterval: Self.tokenFetchTimeout,
                                               repeats: false) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                print("DawgglesAccessorySetup: GATT token fetch timed out — proceeding without credentials")
                self.cleanupBLETokenFetch()
                completion()
            }
        }

        // CBCentralManager calls delegate on .main → compatible with @MainActor.
        centralManager = CBCentralManager(delegate: self, queue: .main)
    }

    private func cleanupBLETokenFetch() {
        tokenFetchTimer?.invalidate()
        tokenFetchTimer = nil
        if let p = tokenPeripheral {
            centralManager?.cancelPeripheralConnection(p)
            tokenPeripheral = nil
        }
        centralManager?.stopScan()
        centralManager = nil
        tokenFetchCompletion = nil
    }

    // MARK: - ASAccessoryEvent handling

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
                // On activation with an already-paired accessory the token is already
                // in the Keychain — skip the GATT fetch and go straight to Wi-Fi.
                joinHotspot(accessory: accessory)
            }
        case .accessoryAdded:
            guard let accessory = event.accessory else { break }
            isPairing = false
            pairedAccessory = accessory
            // Fresh pairing: read token from Pi over BLE before joining Wi-Fi.
            fetchTokenViaBLE {
                self.joinHotspot(accessory: accessory)
            }
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

    // MARK: - Private helpers

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

        let symbol = UIImage(named: "Icon") ?? UIImage()
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

// MARK: - CBCentralManagerDelegate

extension DawgglesAccessorySetup: CBCentralManagerDelegate {

    nonisolated func centralManagerDidUpdateState(_ central: CBCentralManager) {
        Task { @MainActor in
            guard central === self.centralManager else { return }
            switch central.state {
            case .poweredOn:
                // First check for a peripheral that's still connected from the pairing flow.
                let connected = central.retrieveConnectedPeripherals(
                    withServices: [Self.dawgglesServiceUUID]
                )
                if let peripheral = connected.first {
                    print("DawgglesAccessorySetup: found connected peripheral for token read")
                    self.tokenPeripheral = peripheral
                    peripheral.delegate = self
                    central.connect(peripheral, options: nil)
                } else {
                    // Scan — Pi keeps advertising for 30 s after pairing.
                    print("DawgglesAccessorySetup: scanning for Dawggles to read token...")
                    central.scanForPeripherals(withServices: [Self.dawgglesServiceUUID], options: nil)
                }
            case .poweredOff, .unauthorized, .unsupported:
                print("DawgglesAccessorySetup: Bluetooth unavailable (\(central.state.rawValue)) — skipping token fetch")
                let completion = self.tokenFetchCompletion
                self.cleanupBLETokenFetch()
                completion?()
            default:
                break
            }
        }
    }

    nonisolated func centralManager(
        _ central: CBCentralManager,
        didDiscover peripheral: CBPeripheral,
        advertisementData: [String: Any],
        rssi RSSI: NSNumber
    ) {
        Task { @MainActor in
            guard central === self.centralManager, self.tokenPeripheral == nil else { return }
            print("DawgglesAccessorySetup: discovered \(peripheral.name ?? "Dawggles") — connecting for token read")
            central.stopScan()
            self.tokenPeripheral = peripheral
            peripheral.delegate = self
            central.connect(peripheral, options: nil)
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager,
                                    didConnect peripheral: CBPeripheral) {
        Task { @MainActor in
            guard central === self.centralManager else { return }
            print("DawgglesAccessorySetup: connected — discovering services")
            peripheral.discoverServices([Self.dawgglesServiceUUID])
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager,
                                    didFailToConnect peripheral: CBPeripheral,
                                    error: Error?) {
        Task { @MainActor in
            guard central === self.centralManager else { return }
            print("DawgglesAccessorySetup: failed to connect for token read: \(error?.localizedDescription ?? "unknown")")
            let completion = self.tokenFetchCompletion
            self.cleanupBLETokenFetch()
            completion?()
        }
    }
}

// MARK: - CBPeripheralDelegate

extension DawgglesAccessorySetup: CBPeripheralDelegate {

    nonisolated func peripheral(_ peripheral: CBPeripheral,
                                didDiscoverServices error: Error?) {
        Task { @MainActor in
            guard peripheral === self.tokenPeripheral else { return }
            if let error {
                print("DawgglesAccessorySetup: service discovery failed: \(error.localizedDescription)")
                let completion = self.tokenFetchCompletion
                self.cleanupBLETokenFetch()
                completion?()
                return
            }
            guard let service = peripheral.services?.first(where: {
                $0.uuid == Self.dawgglesServiceUUID
            }) else {
                print("DawgglesAccessorySetup: Dawggles GATT service not found")
                let completion = self.tokenFetchCompletion
                self.cleanupBLETokenFetch()
                completion?()
                return
            }
            peripheral.discoverCharacteristics([Self.tokenCharUUID], for: service)
        }
    }

    nonisolated func peripheral(_ peripheral: CBPeripheral,
                                didDiscoverCharacteristicsFor service: CBService,
                                error: Error?) {
        Task { @MainActor in
            guard peripheral === self.tokenPeripheral else { return }
            if let error {
                print("DawgglesAccessorySetup: characteristic discovery failed: \(error.localizedDescription)")
                let completion = self.tokenFetchCompletion
                self.cleanupBLETokenFetch()
                completion?()
                return
            }
            guard let char = service.characteristics?.first(where: {
                $0.uuid == Self.tokenCharUUID
            }) else {
                print("DawgglesAccessorySetup: token characteristic not found")
                let completion = self.tokenFetchCompletion
                self.cleanupBLETokenFetch()
                completion?()
                return
            }
            peripheral.readValue(for: char)
        }
    }

    nonisolated func peripheral(_ peripheral: CBPeripheral,
                                didUpdateValueFor characteristic: CBCharacteristic,
                                error: Error?) {
        Task { @MainActor in
            guard peripheral === self.tokenPeripheral,
                  characteristic.uuid == Self.tokenCharUUID else { return }

            defer {
                let completion = self.tokenFetchCompletion
                self.cleanupBLETokenFetch()
                completion?()
            }

            if let error {
                print("DawgglesAccessorySetup: token read failed: \(error.localizedDescription)")
                return
            }

            guard let data = characteristic.value, data.count == 64 else {
                print("DawgglesAccessorySetup: unexpected token payload length: \(characteristic.value?.count ?? 0)")
                return
            }

            let token       = data[0..<32]
            let fingerprint = data[32..<64]

            DawgglesKeychain.storeToken(Data(token))
            DawgglesKeychain.storeCertFingerprint(Data(fingerprint))
            print("DawgglesAccessorySetup: credentials stored in Keychain ✓")
        }
    }
}
