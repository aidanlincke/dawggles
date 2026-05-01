//
//  PhotoLibrarySaver.swift
//  Dawggles
//
//  Saves a UIImage to the iOS Photos library, requesting add-only permission
//  the first time. Used by the Camera app to persist captures triggered from
//  the goggles' forward button.
//

import Photos
import UIKit

enum PhotoLibrarySaver {
    /// Save `image` to the user's Photos library. The completion is invoked on
    /// the main queue with `true` on a successful write, `false` if permission
    /// was denied or the write failed.
    /// Prompt for add-only Photos access if the user hasn't decided yet.
    /// Safe to call repeatedly — system shows the system prompt only once.
    /// Used at app launch alongside other permission prompts so the camera
    /// capture flow doesn't trigger a surprise dialog later.
    static func requestAddOnlyPermission() {
        requestAddOnlyAuthorization { _ in }
    }

    static func save(image: UIImage, completion: @escaping (Bool) -> Void) {
        requestAddOnlyAuthorization { granted in
            guard granted else {
                DispatchQueue.main.async { completion(false) }
                return
            }
            PHPhotoLibrary.shared().performChanges({
                PHAssetChangeRequest.creationRequestForAsset(from: image)
            }, completionHandler: { ok, error in
                if let error {
                    print("[CAMERA] PhotoLibrary save error: \(error)")
                }
                DispatchQueue.main.async { completion(ok) }
            })
        }
    }

    private static func requestAddOnlyAuthorization(_ done: @escaping (Bool) -> Void) {
        let status = PHPhotoLibrary.authorizationStatus(for: .addOnly)
        switch status {
        case .authorized, .limited:
            done(true)
        case .denied, .restricted:
            done(false)
        case .notDetermined:
            PHPhotoLibrary.requestAuthorization(for: .addOnly) { newStatus in
                done(newStatus == .authorized || newStatus == .limited)
            }
        @unknown default:
            done(false)
        }
    }
}
