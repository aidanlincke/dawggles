import Foundation
import CoreFoundation

enum DebugLog {
    /// Toggle to reduce console noise while debugging.
    static var liveEnabled: Bool = true

    static func live(_ message: @autoclosure () -> String) {
        #if DEBUG
        guard liveEnabled else { return }
        let t = String(format: "%.3f", CFAbsoluteTimeGetCurrent())
        print("[LIVE \(t)] \(message())")
        #endif
    }
}

