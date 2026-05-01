//
//  DawgglesKeychain.swift
//  Dawggles
//
//  Stores and retrieves the per-pairing security credentials:
//    • token          — 32-byte bearer token sent to the Pi on every WebSocket connect
//    • certFingerprint — 32-byte SHA-256 of the Pi's self-signed TLS cert (used for pinning)
//

import Foundation
import Security

enum DawgglesKeychain {
    private static let service       = "dawggles"
    private static let tokenAccount  = "pairing.token"
    private static let certAccount   = "pairing.certFingerprint"

    // MARK: - Write

    static func storeToken(_ token: Data) {
        store(data: token, account: tokenAccount)
    }

    static func storeCertFingerprint(_ fingerprint: Data) {
        store(data: fingerprint, account: certAccount)
    }

    // MARK: - Read

    static func loadToken() -> Data? {
        load(account: tokenAccount)
    }

    static func loadCertFingerprint() -> Data? {
        load(account: certAccount)
    }

    // MARK: - Private helpers

    private static func store(data: Data, account: String) {
        let query: [String: Any] = [
            kSecClass as String:       kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        // Delete any existing item so we can replace it cleanly.
        SecItemDelete(query as CFDictionary)

        var attrs = query
        attrs[kSecValueData as String] = data
        let status = SecItemAdd(attrs as CFDictionary, nil)
        if status != errSecSuccess {
            print("DawgglesKeychain: failed to store '\(account)': \(status)")
        }
    }

    private static func load(account: String) -> Data? {
        let query: [String: Any] = [
            kSecClass as String:        kSecClassGenericPassword,
            kSecAttrService as String:  service,
            kSecAttrAccount as String:  account,
            kSecReturnData as String:   true,
            kSecMatchLimit as String:   kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess else { return nil }
        return result as? Data
    }
}
