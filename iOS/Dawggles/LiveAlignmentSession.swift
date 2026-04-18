//
//  LiveAlignmentSession.swift
//  Dawggles
//
//  Runs periodically while the Pi sends live JPEGs: align live frame to the scan still,
//  pick which ROI is under the screen center, tell the Pi when the index changes.
//

import Combine
import UIKit

private struct IndexHysteresis {
    let consecutiveTicksRequired: Int
    private var streakIndex: Int?
    private var streakCount = 0

    mutating func reset() {
        streakIndex = nil
        streakCount = 0
    }

    /// `true` when `index` has won `consecutiveTicksRequired` ticks in a row.
    mutating func shouldCommit(_ index: Int) -> Bool {
        if index == streakIndex {
            streakCount += 1
        } else {
            streakIndex = index
            streakCount = 1
        }
        return streakCount >= consecutiveTicksRequired
    }
}

final class LiveAlignmentSession: ObservableObject {
    @Published private(set) var lastSentIndex: Int?

    private weak var connection: DawgglesConnection?
    private var referenceImage: UIImage?
    private var groupings: [TranslationGrouping] = []
    private var latestLiveFrame: UIImage?
    private var tick: AnyCancellable?
    private var hysteresis = IndexHysteresis(consecutiveTicksRequired: 4)
    private var lastCommittedIndex: Int?

    func disarm() {
        tick?.cancel()
        tick = nil
        connection = nil
        referenceImage = nil
        groupings = []
        latestLiveFrame = nil
        hysteresis.reset()
        lastCommittedIndex = nil
        lastSentIndex = nil
    }

    /// Start alignment for this scan; requires live JPEGs from the Pi on `connection`.
    func arm(reference: UIImage, groupings rows: [[String: Any]], connection: DawgglesConnection) {
        disarm()
        self.connection = connection
        referenceImage = reference
        groupings = rows.enumerated().compactMap { i, d in TranslationGrouping(dictionary: d, arrayIndex: i) }

        tick = Timer.publish(every: 1.0 / 10.0, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in
                self?.alignmentTick()
            }
    }

    func onLiveFrame(_ image: UIImage) {
        latestLiveFrame = image
    }

    private func alignmentTick() {
        guard let ref = referenceImage,
              let live = latestLiveFrame,
              let conn = connection,
              conn.isConnected,
              !groupings.isEmpty else { return }

        guard let candidate = FocusAlignment.activeGroupingIndex(
            reference: ref,
            live: live,
            groupings: groupings
        ) else {
            hysteresis.reset()
            return
        }

        guard hysteresis.shouldCommit(candidate) else { return }

        if candidate != lastCommittedIndex {
            lastCommittedIndex = candidate
            lastSentIndex = candidate
            conn.sendActiveGroupingIndex(candidate)
        }
    }
}
