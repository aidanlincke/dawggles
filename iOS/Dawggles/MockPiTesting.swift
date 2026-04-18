#if DEBUG
import Foundation

/// Local testing without a Raspberry Pi: run `tools/mock_pi_ws.py` on your Mac, then use a **Debug** scheme with environment variables (see that script’s header).
enum MockPiTesting {
    /// When `1`, skip pairing UI and connect WebSocket to `websocketHost` (Mac mock server).
    static var isEnabled: Bool {
        ProcessInfo.processInfo.environment["DAWGGLES_MOCK_PI"] == "1"
    }

    /// WebSocket host. Default `127.0.0.1` works for **iOS Simulator** → mock server on the same Mac.
    /// On a **physical iPhone**, set to your Mac’s LAN IP (e.g. `192.168.1.42`).
    static var websocketHost: String {
        let h = ProcessInfo.processInfo.environment["DAWGGLES_MOCK_PI_HOST"] ?? ""
        return h.isEmpty ? "127.0.0.1" : h
    }
}
#endif
