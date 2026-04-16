import Foundation
import Combine
import NetworkExtension

class DawgglesConnection: ObservableObject {
    static let shared = DawgglesConnection()

    @Published var status: String = "Idle"
    @Published var isConnected: Bool = false

    private init() {}

    // MARK: - Wi-Fi Connection

    func connectToWiFi(password: String) {
        status = "Configuring Hotspot connection..."

        let hotspotConfig = NEHotspotConfiguration(ssid: "Dawggles", passphrase: password, isWEP: false)
        hotspotConfig.joinOnce = true

        NEHotspotConfigurationManager.shared.apply(hotspotConfig) { [weak self] error in
            DispatchQueue.main.async {
                if let error = error {
                    self?.status = "Failed to join AP: \(error.localizedDescription)"
                    self?.isConnected = false
                } else {
                    self?.status = "✅ Joined Wi-Fi! Connecting TCP..."
                    self?.isConnected = true
                    self?.connectTCP()
                }
            }
        }
    }

    func connectTCP() {
        let ip = "10.42.0.1"
        let port: UInt16 = 12345
        self.status = "✅ Connected to Pi at \(ip):\(port)"
    }
}
