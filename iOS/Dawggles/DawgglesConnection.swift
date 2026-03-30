import Foundation
import CoreBluetooth
import NetworkExtension

class DawgglesConnection: NSObject, ObservableObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    static let shared = DawgglesConnection()
    
    @Published var status: String = "Idle"
    @Published var isConnected: Bool = false
    
    // BLE objects
    private var centralManager: CBCentralManager!
    private var peripheral: CBPeripheral?
    private let serviceUUID = CBUUID(string: "0000d100-0000-1000-8000-00805f9b34fb")
    private let charUUID = CBUUID(string: "0000d101-0000-1000-8000-00805f9b34fb")
    
    override init() {
        super.init()
        // Initialize BLE manager so it can automatically knock on the glasses when the app opens
        centralManager = CBCentralManager(delegate: self, queue: nil)
    }
    
    // ----------------------------------------------------
    // MARK: - STEP 1: The BLE Knock
    // ----------------------------------------------------
    
    func knockOnGlasses() {
        if centralManager.state == .poweredOn {
            status = "Scanning for glasses..."
            centralManager.scanForPeripherals(withServices: [serviceUUID], options: nil)
        }
    }
    
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if central.state == .poweredOn {
            knockOnGlasses()
        } else {
            status = "Bluetooth is not powered on"
        }
    }
    
    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String : Any], rssi RSSI: NSNumber) {
        status = "Found glasses! Connecting..."
        self.peripheral = peripheral
        centralManager.stopScan()
        centralManager.connect(peripheral, options: nil)
    }
    
    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        self.peripheral?.delegate = self
        self.peripheral?.discoverServices([serviceUUID])
    }
    
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard let service = peripheral.services?.first(where: { $0.uuid == serviceUUID }) else { return }
        peripheral.discoverCharacteristics([charUUID], for: service)
    }
    
    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard let characteristic = service.characteristics?.first(where: { $0.uuid == charUUID }) else { return }
        
        status = "Knocking on glasses..."
        
        // We just send a blank string "knock" to the Pi. 
        // This triggers the Python code to wake up the OLED screen!
        let knockData = "knock".data(using: .utf8)!
        peripheral.writeValue(knockData, for: characteristic, type: .withResponse)
    }
    
    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        status = "Knock successful! Type the PIN you see on the glasses."
        
        // We can disconnect from BLE now, we don't need it anymore.
        centralManager.cancelPeripheralConnection(peripheral)
    }
    
    
    // ----------------------------------------------------
    // MARK: - STEP 2: The Wi-Fi Connection
    // ----------------------------------------------------
    
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
