import Foundation
import AVFoundation
import Combine

/// Captures microphone audio and streams 16 kHz mono Int16 PCM via `onAudioBuffer`.
/// Start/stop is driven by WebSocket events from the Pi (mic_activate / mic_deactivate).
class MicrophoneManager: ObservableObject {
    static let shared = MicrophoneManager()

    @Published private(set) var isRecording = false

    /// Called on a background thread with each chunk of raw PCM data ready to send.
    var onAudioBuffer: ((Data) -> Void)?

    private let engine = AVAudioEngine()
    private let targetSampleRate: Double = 16000
    private let targetChannels: AVAudioChannelCount = 1

    private init() {}

    func requestPermission() {
        AVAudioApplication.requestRecordPermission { _ in }
    }

    func start() {
        guard !isRecording else { return }

        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .measurement,
                                    options: [.defaultToSpeaker, .allowBluetooth])
            try session.setActive(true)
        } catch {
            #if DEBUG
            print("[MIC] AVAudioSession setup failed: \(error)")
            #endif
            return
        }

        let inputNode = engine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)

        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: targetSampleRate,
            channels: targetChannels,
            interleaved: true
        ),
        let converter = AVAudioConverter(from: inputFormat, to: targetFormat) else {
            #if DEBUG
            print("[MIC] Failed to create audio converter")
            #endif
            return
        }

        inputNode.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { [weak self] buffer, _ in
            guard let self else { return }
            let outFrames = AVAudioFrameCount(
                Double(buffer.frameLength) * self.targetSampleRate / inputFormat.sampleRate
            )
            guard let converted = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: outFrames) else { return }

            var error: NSError?
            converter.convert(to: converted, error: &error) { _, outStatus in
                outStatus.pointee = .haveData
                return buffer
            }
            guard error == nil, let samples = converted.int16ChannelData else { return }
            let data = Data(bytes: samples[0], count: Int(converted.frameLength) * 2)
            self.onAudioBuffer?(data)
        }

        do {
            try engine.start()
            DispatchQueue.main.async { self.isRecording = true }
            #if DEBUG
            print("[MIC] Recording started")
            #endif
        } catch {
            #if DEBUG
            print("[MIC] Engine start failed: \(error)")
            #endif
            inputNode.removeTap(onBus: 0)
        }
    }

    func stop() {
        guard isRecording else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        DispatchQueue.main.async { self.isRecording = false }
        #if DEBUG
        print("[MIC] Recording stopped")
        #endif
    }
}
