import Foundation
import Speech
import Combine
import CryptoKit
import Network
import UIKit
import Darwin
import AVFoundation

enum ConnectionStatus: Equatable {
    case disconnected, connecting, connected
}

/// WebSocket client to the Pi: camera stream JPEGs (binary), OCR/groupings back, active line index.
class DawgglesConnection: ObservableObject {
    static let shared = DawgglesConnection()

    @Published var isConnected: Bool = false
    @Published private(set) var cameraImage: UIImage? = nil
    @Published private(set) var oledImage: UIImage? = nil

    weak var liveAlignment: LiveAlignmentSession?
    private var sourceLanguageCancellable: AnyCancellable?
    var translationSettings: TranslationSettings? {
        didSet {
            // When the source language changes while the mic is live, restart speech
            // recognition with the new locale — otherwise the recognizer keeps using
            // whatever locale was active at mic_activate time.
            sourceLanguageCancellable = translationSettings?.$selectedSourceIndex
                .dropFirst()
                .receive(on: DispatchQueue.main)
                .sink { [weak self] _ in
                    guard let self, MicrophoneManager.shared.isRecording else { return }
                    MicrophoneManager.shared.stopSpeechRecognition()
                    self.beginSpeechRecognition()
                }
        }
    }

    private var connection: NWConnection?
    private var reconnectWorkItem: DispatchWorkItem?
    private let pathMonitor = NWPathMonitor()
    private let pathMonitorQueue = DispatchQueue(label: "DawgglesConnection.PathMonitor")
    private var pendingRouteInvalidationWorkItem: DispatchWorkItem?
    private let routeInvalidationGracePeriod: TimeInterval = 1.0
    private var retryAttempt = 0
    private var hostCandidates: [String] = []
    private var hostIndex = 0
    private var activeHost: String?
    @Published private(set) var isConnecting = false
    private var hasScheduledRetryForCurrentConnection = false
    private var expectedWiFiIPv4Prefix: String?
    private var expectedWiFiGateway: String?

    var connectionStatus: ConnectionStatus {
        if isConnected { return .connected }
        if isConnecting { return .connecting }
        return .disconnected
    }

    private let websocketPort: NWEndpoint.Port = 8765
    private let defaultHost = "10.42.0.1"
    private let maxRetryAttempts = 8

    private var streamSeq: Int = 0
    private let streamDecodeQueue = DispatchQueue(label: "DawgglesConnection.streamDecode", qos: .userInteractive)
    private var pendingFrameData: Data? = nil
    private var isDecodingFrame = false
    // Main-thread gate: cleared synchronously in tearDownCameraStream so that any
    // frame already mid-decode on streamDecodeQueue cannot overwrite the nil after teardown.
    private var cameraStreamActive: Bool = false

    private init() {
        startPathMonitoring()
    }

    private func startPathMonitoring() {
        pathMonitor.pathUpdateHandler = { [weak self] path in
            DispatchQueue.main.async {
                self?.handlePathUpdate(path)
            }
        }
        pathMonitor.start(queue: pathMonitorQueue)
    }

    private func handlePathUpdate(_ path: NWPath) {
        guard isConnected || isConnecting else { return }

        if let routeIssue = routeIssueReason(for: path) {
            scheduleRouteInvalidationCheck(initialReason: routeIssue)
        } else {
            pendingRouteInvalidationWorkItem?.cancel()
            pendingRouteInvalidationWorkItem = nil
        }
    }

    private func routeIssueReason(for path: NWPath) -> String? {
        if path.status != .satisfied {
            return "path unsatisfied"
        }

        guard let activeHost else { return nil }
        guard let wifiIP = currentWiFiIPv4() else {
            return "wifi IP unavailable"
        }

        // Host should remain reachable on the current Wi-Fi's gateway in this local-topology setup.
        if let gateway = inferredGateway(from: wifiIP), gateway != activeHost {
            return "wifi gateway changed from \(activeHost) to \(gateway)"
        }

        return nil
    }

    private func scheduleRouteInvalidationCheck(initialReason: String) {
        pendingRouteInvalidationWorkItem?.cancel()

        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            guard self.isConnected || self.isConnecting else { return }

            if let currentIssue = self.routeIssueReason(for: self.pathMonitor.currentPath) {
                self.handleNetworkRouteInvalidated(reason: "\(initialReason) (confirmed: \(currentIssue))")
            }
        }

        pendingRouteInvalidationWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + routeInvalidationGracePeriod, execute: work)
    }

    private func handleNetworkRouteInvalidated(reason: String) {
        #if DEBUG
        print("DawgglesConnection: network route invalidated — \(reason)")
        #endif

        pendingRouteInvalidationWorkItem?.cancel()
        pendingRouteInvalidationWorkItem = nil
        reconnectWorkItem?.cancel()
        hasScheduledRetryForCurrentConnection = false
        isConnecting = false
        isConnected = false
        connection?.cancel()
        connection = nil
        activeHost = nil
        handleCameraStopped()
    }

    private func tearDownCameraStream() {
        cameraStreamActive = false
        cameraImage = nil
        liveAlignment?.disarm()
        streamDecodeQueue.async { [weak self] in self?.pendingFrameData = nil }
        #if DEBUG
        print("[LIVE] DawgglesConnection: camera stream torn down")
        #endif
    }

    /// Pi ended the JPEG stream (or equivalent): stop alignment and clear live UI state.
    private func handleCameraStopped() {
        tearDownCameraStream()
    }

    // MARK: - WebSocket

    /// Immediately enters the connecting state (e.g. while the hotspot join is still in progress).
    func beginConnecting() {
        guard !isConnected && !isConnecting else { return }
        isConnecting = true
    }

    func connectWebSocket(expectedGateway: String? = nil, expectedIPv4Prefix: String? = nil) {
        if isConnected {
            print("DawgglesConnection: connect request ignored (already connected)")
            return
        }

        reconnectWorkItem?.cancel()
        retryAttempt = 0
        hostCandidates = []
        hostIndex = 0
        isConnecting = true
        expectedWiFiGateway = expectedGateway
        expectedWiFiIPv4Prefix = expectedIPv4Prefix

        startConnectionAttempt()
    }

    private func startConnectionAttempt() {
        guard let wifiIP = currentWiFiIPv4() else {
            print("DawgglesConnection: no WiFi IPv4 yet (still waiting for AP DHCP?)")
            scheduleReconnectWaitingForWiFi()
            return
        }

        if let expectedPrefix = expectedWiFiIPv4Prefix, !wifiIP.hasPrefix(expectedPrefix) {
            print("DawgglesConnection: WiFi IPv4 \(wifiIP) is not on expected hotspot")
            scheduleReconnectWaitingForWiFi()
            return
        }

        if let expectedGateway = expectedWiFiGateway {
            guard let inferred = inferredGateway(from: wifiIP), inferred == expectedGateway else {
                let inferred = inferredGateway(from: wifiIP) ?? "unknown"
                print("DawgglesConnection: WiFi gateway \(inferred) is not expected \(expectedGateway)")
                scheduleReconnectWaitingForWiFi()
                return
            }
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

    /// Build NWParameters for a WSS connection with certificate pinning.
    /// If no fingerprint is stored in the Keychain the verify block accepts any
    /// certificate — the bearer-token check on the server still applies, so the
    /// connection is authenticated even without pinning.
    private func makeTLSParameters() -> NWParameters {
        let tlsOptions = NWProtocolTLS.Options()
        let verifyQueue = DispatchQueue(label: "DawgglesConnection.TLSVerify")

        if let pinnedFingerprint = DawgglesKeychain.loadCertFingerprint() {
            sec_protocol_options_set_verify_block(
                tlsOptions.securityProtocolOptions,
                { _, secTrust, complete in
                    let trust = sec_trust_copy_ref(secTrust).takeRetainedValue()
                    // Retrieve the leaf certificate from the chain.
                    guard
                        let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
                        let leaf = chain.first
                    else {
                        complete(false)
                        return
                    }
                    // SHA-256 of the DER-encoded certificate — must match the value
                    // delivered at pairing time and stored in the Keychain.
                    let certData    = SecCertificateCopyData(leaf) as Data
                    let fingerprint = Data(SHA256.hash(data: certData))
                    complete(fingerprint == pinnedFingerprint)
                },
                verifyQueue
            )
        }

        let params = NWParameters(tls: tlsOptions, tcp: NWProtocolTCP.Options())
        let wsOptions = NWProtocolWebSocket.Options()
        wsOptions.autoReplyPing = true
        params.defaultProtocolStack.applicationProtocols.insert(wsOptions, at: 0)
        return params
    }

    /// Send the bearer token as the very first WebSocket message so the Pi can
    /// authenticate this connection before routing any application traffic.
    private func sendAuthToken() {
        guard let token = DawgglesKeychain.loadToken() else {
            print("DawgglesConnection: no auth token in Keychain — connection will be rejected by Pi")
            return
        }
        sendJSON(["type": "auth", "token": token.base64EncodedString()])
    }

    private func openWebSocket(host: String, logLabel: String) {
        guard let wsURL = URL(string: "wss://\(host):\(websocketPort.rawValue)/") else {
            print("DawgglesConnection: invalid WebSocket URL for host \(host)")
            isConnecting = false
            return
        }

        connection?.cancel()
        hasScheduledRetryForCurrentConnection = false

        let params = makeTLSParameters()

        let conn = NWConnection(to: .url(wsURL), using: params)
        connection = conn
        activeHost = host

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
                    self.sendAuthToken()   // must be first — Pi requires auth before routing
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
                    self.activeHost = nil
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
                    self?.activeHost = nil
                    self?.handleCameraStopped()
                    #if DEBUG
                    print("[LIVE] DawgglesConnection: receiveMessage error -> disconnected, ending live stream")
                    #endif
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
                    self.streamSeq += 1
                    DispatchQueue.main.async { self.cameraStreamActive = true }
                    // Per-frame logging removed to avoid console spam during streaming.
                    self.streamDecodeQueue.async { [weak self] in
                        guard let self else { return }
                        self.pendingFrameData = data
                        if !self.isDecodingFrame { self.drainPreviewMailbox() }
                    }
                default:
                    break
                }
            }
            self?.receiveMessage()
        }
    }

    // Called only on streamDecodeQueue. Grabs the latest pending frame, decodes it,
    // publishes to main, then re-checks for another frame that may have arrived during decode.
    private func drainPreviewMailbox() {
        guard let data = pendingFrameData else {
            isDecodingFrame = false
            return
        }
        pendingFrameData = nil
        isDecodingFrame = true

        guard let image = UIImage(data: data) else {
            #if DEBUG
            print("[LIVE] DawgglesConnection: camera JPEG decode failed bytes=\(data.count)")
            #endif
            streamDecodeQueue.async { [weak self] in self?.drainPreviewMailbox() }
            return
        }

        DispatchQueue.main.async { [weak self] in
            guard let self, self.cameraStreamActive else { return }
            self.liveAlignment?.arm(connection: self)
            self.cameraImage = image
            self.liveAlignment?.onLiveFrame(image)
        }
        streamDecodeQueue.async { [weak self] in self?.drainPreviewMailbox() }
    }

    private func handleJSON(_ text: String) {
        guard let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }

        if obj["event"] as? String == "oled_frame",
           let b64 = obj["buffer_b64"] as? String,
           let buf = Data(base64Encoded: b64),
           let image = Self.oledBufferToImage(buf) {
            DispatchQueue.main.async { self.oledImage = image }
            return
        }

        if obj["event"] as? String == "camera_stopped" {
            DispatchQueue.main.async { self.handleCameraStopped() }
            #if DEBUG
            print("[LIVE] DawgglesConnection: received camera_stopped")
            #endif
            return
        }

        if obj["event"] as? String == "mic_activate" {
            DispatchQueue.main.async { self.activateMic() }
            return
        }

        if obj["event"] as? String == "mic_deactivate" {
            DispatchQueue.main.async { self.deactivateMic() }
            return
        }

        if obj["app"] as? String == "camera", obj["event"] as? String == "capture" {
            DispatchQueue.main.async { self.handleCameraCapture() }
            return
        }

    }

    // MARK: - Camera capture (Camera app)

    /// Snapshot the most recent live frame and write it to the iOS Photos library.
    /// Sends `capture_saved` / `capture_failed` back to the Pi so the OLED can show feedback.
    private func handleCameraCapture() {
        guard let image = cameraImage else {
            #if DEBUG
            print("[CAMERA] capture requested but no live frame available")
            #endif
            sendJSON(["app": "camera", "event": "capture_failed", "reason": "no_frame"])
            return
        }
        PhotoLibrarySaver.save(image: image) { [weak self] success in
            guard let self else { return }
            self.sendJSON([
                "app": "camera",
                "event": success ? "capture_saved" : "capture_failed"
            ])
            #if DEBUG
            print("[CAMERA] capture \(success ? "saved" : "failed")")
            #endif
        }
    }

    // MARK: - Mic

    private func activateMic() {
        MicrophoneManager.shared.start()
        beginSpeechRecognition()
    }

    private func beginSpeechRecognition() {
        MicrophoneManager.shared.startSpeechRecognition(locale: resolvedSpeechLocale()) { [weak self] text, _ in
            guard let self else { return }
            // Same-language (captions mode): bypass the translation task entirely.
            // Apple's translationTask closure does not fire reliably for an
            // identical source/target pair, which would stall the isTranslating
            // flag and drop all subsequent speech updates.
            let isSameLanguage: Bool = {
                guard let s = self.translationSettings, s.selectedSourceIndex != 0 else { return false }
                return TranslationSettings.sourceLanguageCodes[s.selectedSourceIndex] ==
                       TranslationSettings.targetLanguageCodes[s.selectedTargetIndex]
            }()
            if isSameLanguage {
                self.sendJSON(["app": "translation", "event": "speech_text", "text": text])
            } else {
                Translator.shared.translate(text) { [weak self] translatedText in
                    self?.sendJSON([
                        "app": "translation",
                        "event": "speech_text",
                        "text": translatedText
                    ])
                }
            }
        }
    }

    private func deactivateMic() {
        MicrophoneManager.shared.stopSpeechRecognition()
        MicrophoneManager.shared.stop()
    }

    private func resolvedSpeechLocale() -> Locale {
        guard let settings = translationSettings else { return .current }
        if settings.selectedSourceIndex == 0 {
            // Auto — switch picker to device locale
            let code = Locale.current.language.languageCode?.identifier ?? "en"
            let idx = TranslationSettings.sourceLanguageCodes.firstIndex(of: code)
                   ?? TranslationSettings.sourceLanguageCodes.firstIndex(of: "en")
                   ?? 1
            settings.selectedSourceIndex = idx
            return Self.speechLocale(for: TranslationSettings.sourceLanguageCodes[idx])
        }
        return Self.speechLocale(for: TranslationSettings.sourceLanguageCodes[settings.selectedSourceIndex])
    }

    /// Maps bare language codes to the region-specific locale that
    /// SpeechTranscriber.supportedLocales actually lists. Without a region
    /// suffix, supportedLocale(equivalentTo:) returns nil and recognition
    /// silently falls back to English.
    private static func speechLocale(for code: String) -> Locale {
        let regionMap: [String: String] = [
            "en": "en-US",
            "es": "es-ES",
            "zh": "zh-CN",
            "fr": "fr-FR",
            "de": "de-DE",
            "ja": "ja-JP",
            "ko": "ko-KR",
            "ru": "ru-RU",
        ]
        return Locale(identifier: regionMap[code] ?? code)
    }


    // MARK: - OLED decode

    private static func oledBufferToImage(_ buffer: Data) -> UIImage? {
        guard buffer.count == 1024 else { return nil }
        let width = 128, height = 64
        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        for page in 0..<8 {
            for x in 0..<width {
                let byte = buffer[page * width + x]
                for bit in 0..<8 {
                    let v: UInt8 = ((byte >> bit) & 1) == 1 ? 255 : 0
                    let idx = ((page * 8 + bit) * width + x) * 4
                    pixels[idx] = v; pixels[idx+1] = v; pixels[idx+2] = v; pixels[idx+3] = 255
                }
            }
        }
        let colorSpace = CGColorSpaceCreateDeviceGray()
        var grayPixels = [UInt8](repeating: 0, count: width * height)
        for i in 0..<(width * height) { grayPixels[i] = pixels[i * 4] }
        guard let ctx = CGContext(data: &grayPixels, width: width, height: height,
                                  bitsPerComponent: 8, bytesPerRow: width,
                                  space: colorSpace,
                                  bitmapInfo: CGImageAlphaInfo.none.rawValue),
              let cgImage = ctx.makeImage() else { return nil }
        return UIImage(cgImage: cgImage)
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

    func sendTranslationPayload(data: String, groupings: [[String: Any]]) {
        sendJSON([
            "app": "translation",
            "data": data,
            "groupings": groupings,
        ])
    }

    // MARK: - Disconnect

    func disconnect() {
        deactivateMic()
        pendingRouteInvalidationWorkItem?.cancel()
        pendingRouteInvalidationWorkItem = nil
        reconnectWorkItem?.cancel()
        connection?.cancel()
        connection = nil
        activeHost = nil
        isConnecting = false
        hasScheduledRetryForCurrentConnection = false
        DispatchQueue.main.async {
            self.isConnected = false
            self.handleCameraStopped()
        }
    }
}

private extension Array {
    subscript(safe index: Int) -> Element? {
        guard indices.contains(index) else { return nil }
        return self[index]
    }
}
