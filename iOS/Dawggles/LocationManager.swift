//
//  LocationManager.swift
//  Dawggles
//

import Foundation
import CoreLocation
import Combine

/// Singleton wrapper around `CLLocationManager` that requests Always + precise
/// location authorization and publishes the user's current location.
///
/// Authorization is requested in two stages, which is how iOS requires it:
///   1. First call escalates `.notDetermined` → `.authorizedWhenInUse`.
///   2. Once `.authorizedWhenInUse` is granted, we re-prompt for `.authorizedAlways`.
///
/// When `.authorizedAlways` is granted we enable background location updates so
/// the app can keep receiving updates while suspended. This relies on
/// `UIBackgroundModes = location` in `Info.plist`.
@MainActor
final class LocationManager: NSObject, ObservableObject {
    static let shared = LocationManager()

    @Published private(set) var location: CLLocation?
    @Published private(set) var authorizationStatus: CLAuthorizationStatus
    @Published private(set) var accuracyAuthorization: CLAccuracyAuthorization

    private let manager = CLLocationManager()
    private var didRequestAlwaysEscalation = false

    override init() {
        self.authorizationStatus = manager.authorizationStatus
        self.accuracyAuthorization = manager.accuracyAuthorization
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        manager.distanceFilter = 25
        manager.pausesLocationUpdatesAutomatically = true
        manager.activityType = .other
    }

    /// Idempotently kicks off the authorization flow. Safe to call repeatedly
    /// (e.g. on every appearance of the paired dashboard).
    func requestAlwaysAuthorization() {
        switch manager.authorizationStatus {
        case .notDetermined:
            manager.requestWhenInUseAuthorization()
        case .authorizedWhenInUse:
            if !didRequestAlwaysEscalation {
                didRequestAlwaysEscalation = true
                manager.requestAlwaysAuthorization()
            }
            startUpdatingIfAuthorized()
        case .authorizedAlways:
            startUpdatingIfAuthorized()
        case .denied, .restricted:
            break
        @unknown default:
            break
        }

        if manager.accuracyAuthorization == .reducedAccuracy {
            manager.requestTemporaryFullAccuracyAuthorization(withPurposeKey: "NavigationSearch")
        }
    }

    private func startUpdatingIfAuthorized() {
        let status = manager.authorizationStatus
        guard status == .authorizedAlways || status == .authorizedWhenInUse else { return }
        if status == .authorizedAlways {
            manager.allowsBackgroundLocationUpdates = true
            manager.showsBackgroundLocationIndicator = false
        }
        manager.startUpdatingLocation()
    }
}

extension LocationManager: CLLocationManagerDelegate {
    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        let accuracy = manager.accuracyAuthorization
        Task { @MainActor in
            self.authorizationStatus = status
            self.accuracyAuthorization = accuracy
            switch status {
            case .authorizedWhenInUse:
                if !self.didRequestAlwaysEscalation {
                    self.didRequestAlwaysEscalation = true
                    self.manager.requestAlwaysAuthorization()
                }
                self.startUpdatingIfAuthorized()
            case .authorizedAlways:
                self.startUpdatingIfAuthorized()
            default:
                break
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let last = locations.last else { return }
        Task { @MainActor in
            self.location = last
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        // Transient errors (e.g. kCLErrorLocationUnknown) are expected; ignore.
    }
}
