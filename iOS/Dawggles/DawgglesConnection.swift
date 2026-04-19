import Foundation
import Combine
import Network
import UIKit
import Darwin

/// WebSocket client to the Pi: still images (JSON), live JPEGs (binary), OCR/groupings back, active line index.
class DawgglesConnection: ObservableObject {
    static let shared = DawgglesConnection()

    @Published var isConnected: Bool = false
    @Published var receivedImage: UIImage? = nil
    @Published private(set) var previewImage: UIImage? = nil
    @Published var receivedTranslation: String?

    weak var liveAlignment: LiveAlignmentSession?

    private var connection: NWConnection?
    private var reconnectWorkItem: DispatchWorkItem?
    private var retryAttempt = 0
    private var hostCandidates: [String] = []
    private var hostIndex = 0
    @Published private(set) var isConnecting = false
    private var hasScheduledRetryForCurrentConnection = false

    private let websocketPort: NWEndpoint.Port = 8765
    private let defaultHost = "10.42.0.1"
    private let maxRetryAttempts = 8

    private var lastPreviewWall: CFAbsoluteTime = 0
    private let previewMinInterval: TimeInterval = 1.0 / 15.0

    // MARK: Live stream + alignment (Pi binary JPEGs)

    /// After shutter still, wait for the first preview JPEG before arming live OCR (no still-frame OCR).
    private var pendingTranslationLiveArm = false
    /// Pi sent `preview_stopped` or we disconnected — don't arm alignment when OCR completes late.
    private var suppressAlignmentArmUntilNextStill = false

    private init() {}

    private func tearDownLiveAlignmentAndPreview() {
        pendingTranslationLiveArm = false
        previewImage = nil
        liveAlignment?.disarm()
    }

    /// Pi ended the JPEG stream (or equivalent): stop alignment and clear live UI state.
    private func handlePiLiveStreamEnded() {
        suppressAlignmentArmUntilNextStill = true
        tearDownLiveAlignmentAndPreview()
    }

    // MARK: - WebSocket

    func connectWebSocket() {
        if isConnected || isConnecting {
            print("DawgglesConnection: connect request ignored (already connected/connecting)")
            return
        }

        reconnectWorkItem?.cancel()
        retryAttempt = 0
        hostCandidates = []
        hostIndex = 0
        isConnecting = true

        startConnectionAttempt()
    }

    private func startConnectionAttempt() {
        #if DEBUG
        if MockPiTesting.isEnabled {
            hostCandidates = [MockPiTesting.websocketHost]
            hostIndex = 0
            openWebSocket(host: MockPiTesting.websocketHost, logLabel: "mock Pi (Mac)")
            return
        }
        #endif

        guard let wifiIP = currentWiFiIPv4() else {
            print("DawgglesConnection: no WiFi IPv4 yet (still waiting for AP DHCP?)")
            scheduleReconnectWaitingForWiFi()
            return
        }

        let refreshedCandidates = makeHostCandidates(wifiIPv4: wifiIP)
        if refreshedCandidates != hostCandidates {
            hostCandidates = refreshedCandidates
            hostIndex = 0
        }

        print("DawgglesConnection: WiFi IPv4 is \(wifiIP)")

        let host = hostCandidates[safe: hostIndex] ?? defaultHost
        openWebSocket(host: host, logLabel: "hotspot")
    }

    private func openWebSocket(host: String, logLabel: String) {
        guard let wsURL = URL(string: "ws://\(host):\(websocketPort.rawValue)/") else {
            print("DawgglesConnection: invalid WebSocket URL for host \(host)")
            isConnecting = false
            return
        }

        connection?.cancel()
        hasScheduledRetryForCurrentConnection = false

        let params = NWParameters.tcp
        let wsOptions = NWProtocolWebSocket.Options()
        wsOptions.autoReplyPing = true
        params.defaultProtocolStack.applicationProtocols.insert(wsOptions, at: 0)

        let conn = NWConnection(to: .url(wsURL), using: params)
        connection = conn

        conn.stateUpdateHandler = { [weak self] state in
            print("DawgglesConnection state → \(state)")
            DispatchQueue.main.async {
                guard let self else { return }
                guard self.connection === conn else { return }

                switch state {
                case .setup:
                    print("DawgglesConnection: setup")
                case .preparing:
                    print("DawgglesConnection: preparing")
                case .ready:
                    print("DawgglesConnection: ready")
                    self.isConnected = true
                    self.retryAttempt = 0
                    self.isConnecting = false
                    self.receiveMessage()
                case .waiting(let error):
                    print("DawgglesConnection: waiting — \(error)")
                    self.isConnected = false
                    self.scheduleReconnect(after: self.retryDelay(), reason: error)
                case .failed(let error):
                    print("DawgglesConnection: failed — \(error)")
                    self.isConnected = false
                    self.scheduleReconnect(after: self.retryDelay(), reason: error)
                case .cancelled:
                    print("DawgglesConnection: cancelled")
                    self.isConnected = false
                    self.connection = nil
                    self.isConnecting = false
                @unknown default:
                    break
                }
            }
        }

        print("DawgglesConnection: connecting to \(host):\(websocketPort.rawValue) (\(logLabel))")
        conn.start(queue: .global(qos: .userInitiated))
    }

    private func scheduleReconnectWaitingForWiFi() {
        guard !hasScheduledRetryForCurrentConnection else { return }
        hasScheduledRetryForCurrentConnection = true

        guard retryAttempt < maxRetryAttempts else {
            print("DawgglesConnection: retry limit reached while waiting for WiFi IPv4")
            connection = nil
            isConnecting = false
            return
        }

        retryAttempt += 1
        reconnectWorkItem?.cancel()

        let delay = retryDelay()
        let work = DispatchWorkItem { [weak self] in
            self?.hasScheduledRetryForCurrentConnection = false
            self?.startConnectionAttempt()
        }
        reconnectWorkItem = work

        print("DawgglesConnection: waiting for WiFi IPv4, retry \(retryAttempt)/\(maxRetryAttempts) in \(String(format: "%.1f", delay))s")
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    private func scheduleReconnect(after delay: TimeInterval, reason: NWError) {
        guard !hasScheduledRetryForCurrentConnection else { return }
        hasScheduledRetryForCurrentConnection = true

        guard shouldRetry(for: reason) else {
            print("DawgglesConnection: not retrying for error \(reason)")
            connection = nil
            isConnecting = false
            return
        }
        guard retryAttempt < maxRetryAttempts else {
            print("DawgglesConnection: retry limit reached")
            connection = nil
            isConnecting = false
            return
        }

        retryAttempt += 1
        reconnectWorkItem?.cancel()

        if hostCandidates.count > 1 {
            hostIndex = (hostIndex + 1) % hostCandidates.count
        }

        let work = DispatchWorkItem { [weak self] in
            self?.hasScheduledRetryForCurrentConnection = false
            self?.startConnectionAttempt()
        }
        reconnectWorkItem = work

        print("DawgglesConnection: retry \(retryAttempt)/\(maxRetryAttempts) in \(String(format: "%.1f", delay))s")
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    private func retryDelay() -> TimeInterval {
        min(pow(2.0, Double(retryAttempt)) * 0.5, 4.0)
    }

    private func shouldRetry(for error: NWError) -> Bool {
        switch error {
        case .posix(let code):
            return code == .ENETDOWN ||
                   code == .ENETUNREACH ||
                   code == .EHOSTUNREACH ||
                   code == .ETIMEDOUT ||
                   code == .ECONNABORTED ||
                   code == .ECONNREFUSED
        default:
            return true
        }
    }

    private func makeHostCandidates(wifiIPv4: String) -> [String] {
        var candidates: [String] = []

        if let inferredGateway = inferredGateway(from: wifiIPv4) {
            candidates.append(inferredGateway)
        }

        if !candidates.contains(defaultHost) {
            candidates.append(defaultHost)
        }

        return candidates
    }

    private func inferredGateway(from wifiIPv4: String) -> String? {
        let parts = wifiIPv4.split(separator: ".")
        guard parts.count == 4 else { return nil }
        return "\(parts[0]).\(parts[1]).\(parts[2]).1"
    }

    private func currentWiFiIPv4() -> String? {
        var addressList: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&addressList) == 0, let first = addressList else { return nil }
        defer { freeifaddrs(addressList) }

        for interface in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let flags = Int32(interface.pointee.ifa_flags)
            let isUp = (flags & IFF_UP) == IFF_UP
            let isRunning = (flags & IFF_RUNNING) == IFF_RUNNING
            guard isUp, isRunning else { continue }

            let name = String(cString: interface.pointee.ifa_name)
            guard name == "en0" else { continue }
            guard let addr = interface.pointee.ifa_addr, addr.pointee.sa_family == UInt8(AF_INET) else { continue }

            var hostname = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            let result = getnameinfo(
                addr,
                socklen_t(addr.pointee.sa_len),
                &hostname,
                socklen_t(hostname.count),
                nil,
                0,
                NI_NUMERICHOST
            )
            if result == 0 {
                return String(cString: hostname)
            }
        }
        return nil
    }

    private func receiveMessage() {
        connection?.receiveMessage { [weak self] data, context, _, error in
            if error != nil {
                DispatchQueue.main.async {
                    self?.isConnected = false
                    self?.handlePiLiveStreamEnded()
                }
                return
            }
            if let data, !data.isEmpty,
               let meta = context?.protocolMetadata(definition: NWProtocolWebSocket.definition) as? NWProtocolWebSocket.Metadata {
                switch meta.opcode {
                case .text:
                    if let text = String(data: data, encoding: .utf8) {
                        self?.handleJSON(text)
                    }
                case .binary:
                    guard let self else { break }
                    let now = CFAbsoluteTimeGetCurrent()
                    if now - self.lastPreviewWall >= self.previewMinInterval,
                       let image = UIImage(data: data) {
                        self.lastPreviewWall = now
                        DispatchQueue.main.async { [weak self] in
                            guard let self else { return }
                            if self.pendingTranslationLiveArm, !self.suppressAlignmentArmUntilNextStill {
                                self.pendingTranslationLiveArm = false
                                self.liveAlignment?.arm(connection: self)
                            }
                            self.previewImage = image
                            self.liveAlignment?.onLiveFrame(image)
                        }
                    }
                default:
                    break
                }
            }
            self?.receiveMessage()
        }
    }

    private func handleJSON(_ text: String) {
        guard let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }

        if obj["event"] as? String == "preview_stopped" {
            DispatchQueue.main.async { self.handlePiLiveStreamEnded() }
            return
        }

        if obj["event"] as? String == "picture",
           let b64 = obj["image_b64"] as? String,
           let imageData = Data(base64Encoded: b64),
           let image = UIImage(data: imageData) {
            DispatchQueue.main.async {
                self.receivedImage = image
                self.receivedTranslation = nil
                self.suppressAlignmentArmUntilNextStill = false
            }
            let app = obj["app"] as? String ?? "translation"
            if app == "translation" {
                Task { await self.beginTranslationLiveSessionAfterStill() }
            }
        } else if let groupings = obj["groupings"] as? [[String: Any]] {
            ImageTranslator.shared.liveGroupingsToTranslate = groupings
            ImageTranslator.shared.liveTranslationTrigger = UUID()
        }
    }

    /// Disarm prior session; Pi shows "Processing…" until the first live-frame OCR payload arrives.
    private func beginTranslationLiveSessionAfterStill() async {
        await MainActor.run {
            self.liveAlignment?.disarm()
            if !self.suppressAlignmentArmUntilNextStill {
                self.pendingTranslationLiveArm = true
            }
        }
    }

    // MARK: - Send

    func sendJSON(_ obj: [String: Any]) {
        guard let conn = connection,
              let data = try? JSONSerialization.data(withJSONObject: obj),
              let payload = String(data: data, encoding: .utf8)?.data(using: .utf8) else { return }
        let meta = NWProtocolWebSocket.Metadata(opcode: .text)
        let context = NWConnection.ContentContext(identifier: "json", metadata: [meta])
        conn.send(content: payload, contentContext: context, isComplete: true, completion: .idempotent)
    }

    /// Full translation update from live OCR: groupings, summary `data`, and which line is nearest the image center.
    func sendTranslationPayload(data: String, groupings: [[String: Any]], activeIdx: Int) {
        sendJSON([
            "app": "translation",
            "data": data,
            "groupings": groupings,
            "active_idx": activeIdx,
        ])
    }

    /// Tell the Pi which `groupings` row is active (optional; live path uses `sendTranslationPayload`).
    func sendActiveGroupingIndex(_ index: Int) {
        sendJSON([
            "app": "translation",
            "event": "focus",
            "active_idx": index,
        ])
    }

    // MARK: - Disconnect

    func disconnect() {
        reconnectWorkItem?.cancel()
        connection?.cancel()
        connection = nil
        isConnecting = false
        hasScheduledRetryForCurrentConnection = false
        DispatchQueue.main.async {
            self.isConnected = false
            self.receivedTranslation = nil
            self.handlePiLiveStreamEnded()
        }
    }
}

private extension Array {
    subscript(safe index: Int) -> Element? {
        guard indices.contains(index) else { return nil }
        return self[index]
    }
}
