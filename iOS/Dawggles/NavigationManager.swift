import MapKit
import Combine

/// Manages a walking navigation session: requests a route via MKDirections,
/// tracks the user's position against step endpoints, auto-advances steps,
/// and streams GPS data to the connected RPi goggles.
@MainActor
final class NavigationManager {
    static let shared = NavigationManager()

    private var route: MKRoute?
    private var stepIndex = 0
    private var locationCancellable: AnyCancellable?
    private weak var connection: DawgglesConnection?
    private weak var locationManager: LocationManager?

    private init() {}

    // MARK: - Public

    func start(to mapItem: MKMapItem, connection: DawgglesConnection, locationManager: LocationManager) {
        stop()
        self.connection = connection
        self.locationManager = locationManager

        let request = MKDirections.Request()
        request.source = MKMapItem.forCurrentLocation()
        request.destination = mapItem
        request.transportType = .walking

        MKDirections(request: request).calculate { [weak self] response, _ in
            guard let route = response?.routes.first else { return }
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.route = route
                self.stepIndex = 0
                self.subscribeToLocation()
                self.sendCurrentStep()
            }
        }
    }

    /// Resolves an address string via MKLocalSearch then starts navigation.
    func start(toAddressString address: String, near location: CLLocation,
               connection: DawgglesConnection, locationManager: LocationManager) {
        let request = MKLocalSearch.Request()
        request.naturalLanguageQuery = address
        request.region = MKCoordinateRegion(
            center: location.coordinate,
            latitudinalMeters: 50_000,
            longitudinalMeters: 50_000
        )
        MKLocalSearch(request: request).start { [weak self] response, _ in
            guard let item = response?.mapItems.first else { return }
            Task { @MainActor [weak self] in
                self?.start(to: item, connection: connection, locationManager: locationManager)
            }
        }
    }

    func stop() {
        if route != nil {
            connection?.sendJSON(["app": "gps", "data": NSNull()])
        }
        route = nil
        stepIndex = 0
        locationCancellable = nil
        connection = nil
        locationManager = nil
    }

    // MARK: - Debug

#if DEBUG
    private var debugCycleTask: Task<Void, Never>?
    private let debugIcons = ["turn_left", "turn_right", "straight"]
    private var debugIconIndex = 0

    func startDebugCycle(connection: DawgglesConnection) {
        debugCycleTask?.cancel()
        self.connection = connection
        debugIconIndex = 0
        debugCycleTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                let icon = self.debugIcons[self.debugIconIndex % self.debugIcons.count]
                self.debugIconIndex += 1
                connection.sendJSON([
                    "app": "gps",
                    "data": [
                        "icon_type": icon,
                        "distance": "0.3mi",
                        "street": "Test St",
                        "lines": [] as [[Int]]
                    ] as [String: Any]
                ])
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            }
        }
    }

    func stopDebugCycle() {
        debugCycleTask?.cancel()
        debugCycleTask = nil
        connection?.sendJSON(["app": "gps", "data": NSNull()])
        connection = nil
    }
#endif

    // MARK: - Private

    private func subscribeToLocation() {
        guard let locationManager else { return }
        locationCancellable = locationManager.$location
            .compactMap { $0 }
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.onLocationUpdate($0) }
    }

    private func onLocationUpdate(_ location: CLLocation) {
        guard let route, stepIndex < route.steps.count - 1 else { return }
        let step = route.steps[stepIndex]
        guard step.polyline.pointCount > 0 else { return }

        let endPoint = step.polyline.points()[step.polyline.pointCount - 1]
        let endLocation = CLLocation(latitude: endPoint.coordinate.latitude,
                                     longitude: endPoint.coordinate.longitude)
        if location.distance(from: endLocation) < 30 {
            stepIndex += 1
            sendCurrentStep()
        }
    }

    private func sendCurrentStep() {
        guard let route, let connection, stepIndex < route.steps.count else { return }
        let step = route.steps[stepIndex]
        connection.sendJSON([
            "app": "gps",
            "data": [
                "icon_type": iconType(for: step),
                "distance": formatDistance(step.distance),
                "street": extractStreet(from: step.instructions),
                "lines": projectPolyline(route.polyline)
            ] as [String: Any]
        ])
    }

    // MARK: - Formatting

    private func iconType(for step: MKRoute.Step) -> String {
        let inst = step.instructions.lowercased()
        if inst.contains("turn left") { return "turn_left" }
        if inst.contains("turn right") { return "turn_right" }
        return "straight"
    }

    private func formatDistance(_ meters: CLLocationDistance) -> String {
        guard meters > 0 else { return "0ft" }
        let feet = Int(meters * 3.28084)
        if feet < 1000 { return "\(feet)ft" }
        let miles = meters / 1609.34
        if miles >= 10 { return "\(Int(miles))mi" }
        return String(format: "%.1fmi", miles)
    }

    private func extractStreet(from instructions: String) -> String {
        for prep in ["onto ", "on ", "along ", "at "] {
            if let range = instructions.range(of: prep, options: .caseInsensitive) {
                return String(instructions[range.upperBound...])
            }
        }
        return instructions
    }

    // MARK: - Minimap projection

    /// Projects the route polyline into OLED minimap coordinates (X∈[0,127], Y∈[29,62])
    /// and returns deduplicated line segments as [[x1,y1,x2,y2]].
    private func projectPolyline(_ polyline: MKPolyline) -> [[Int]] {
        let count = polyline.pointCount
        guard count >= 2 else { return [] }

        var coords = [CLLocationCoordinate2D](repeating: kCLLocationCoordinate2DInvalid, count: count)
        polyline.getCoordinates(&coords, range: NSRange(location: 0, length: count))

        let lats = coords.map { $0.latitude }
        let lons = coords.map { $0.longitude }
        guard let minLat = lats.min(), let maxLat = lats.max(),
              let minLon = lons.min(), let maxLon = lons.max() else { return [] }

        let latSpan = maxLat - minLat
        let lonSpan = maxLon - minLon

        func project(_ c: CLLocationCoordinate2D) -> (Int, Int) {
            let x = lonSpan < 1e-9 ? 64 : Int(((c.longitude - minLon) / lonSpan) * 127)
            let y = latSpan < 1e-9 ? 45 : Int(((maxLat - c.latitude) / latSpan) * 33) + 29
            return (x, y)
        }

        var lines = [[Int]]()
        var prev = project(coords[0])
        for i in 1..<count {
            let curr = project(coords[i])
            if curr != prev {
                lines.append([prev.0, prev.1, curr.0, curr.1])
                prev = curr
            }
        }
        return lines
    }
}
