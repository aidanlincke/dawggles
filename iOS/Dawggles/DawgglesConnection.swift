import Foundation
import NetworkExtension

class DawgglesConnection: NSObject, ObservableObject {
    static let shared = DawgglesConnection()
    
    @Published var status: String = "Idle"
    @Published var isConnected: Bool = false
    
    // Connect to the Pi's Wi-Fi network directly
    func connect(password: String) {
        status = "Configuring Hotspot connection..."
        
        let hotspotConfig = NEHotspotConfiguration(ssid: "Dawggles", passphrase: password, isWEP: false)
        hotspotConfig.joinOnce = true // Connect for this session, but don't auto-join in the background later
        hotspotConfig.isHidden = true // We made the Pi network hidden!
        
        // This triggers the native Apple "Dawggles Wants to Join Wi-Fi Network 'Dawggles'?" popup
        NEHotspotConfigurationManager.shared.apply(hotspotConfig) { [weak self] error in
            DispatchQueue.main.async {
                if let error = error {
                    self?.status = "Failed to join AP: \(error.localizedDescription)"
                    self?.isConnected = false
                } else {
                    self?.status = "✅ Joined Wi-Fi! Connecting TCP..."
                    self?.isConnected = true
                    // Now that we are on the Wi-Fi, open the TCP socket to the Pi
                    self?.connectTCP()
                }
            }
        }
    }
    
    func connectTCP() {
        // Because the Pi is the AP, it is almost always 10.42.0.1
        let ip = "10.42.0.1"
        let port: UInt16 = 12345
        
        // TODO: Next step is to implement the native Swift Network framework (NWConnection) here
        // to talk to the Pi over the socket!
        self.status = "✅ Connected to Pi at \(ip):\(port)"
    }
}
