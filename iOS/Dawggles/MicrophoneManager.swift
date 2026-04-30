import Foundation
import AVFoundation
import Speech
import Combine

/// Captures microphone audio and streams 16 kHz mono Int16 PCM via `onAudioBuffer`.
/// Optionally runs live speech recognition and delivers results via `onSpeechResult`.
/// Start/stop is driven by WebSocket events from the Pi (mic_activate / mic_deactivate).
class MicrophoneManager: ObservableObject {
    static let shared = MicrophoneManager()

    @Published private(set) var isRecording = false

    /// Called on a background thread with each chunk of raw PCM data ready to send.
    var onAudioBuffer: ((Data) -> Void)?
    /// Called on the main thread with (text, isFinal) when speech is recognized.
    var onSpeechResult: ((String, Bool) -> Void)?

    private let engine = AVAudioEngine()
    private let targetSampleRate: Double = 16000
    private let targetChannels: AVAudioChannelCount = 1

    private var recognizer: SFSpeechRecognizer?
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var inputFormat: AVAudioFormat?

    private init() {}

    func requestPermission() {
        AVAudioApplication.requestRecordPermission { _ in }
        SFSpeechRecognizer.requestAuthorization { _ in }
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
        let nativeFormat = inputNode.outputFormat(forBus: 0)
        inputFormat = nativeFormat

        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: targetSampleRate,
            channels: targetChannels,
            interleaved: true
        ),
        let converter = AVAudioConverter(from: nativeFormat, to: targetFormat) else {
            #if DEBUG
            print("[MIC] Failed to create audio converter")
            #endif
            return
        }

        inputNode.installTap(onBus: 0, bufferSize: 4096, format: nativeFormat) { [weak self] buffer, _ in
            guard let self else { return }

            // Feed speech recognizer (uses native format)
            self.recognitionRequest?.append(buffer)

            // Convert and stream PCM to Pi
            let outFrames = AVAudioFrameCount(
                Double(buffer.frameLength) * self.targetSampleRate / nativeFormat.sampleRate
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
        stopSpeechRecognition()
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        DispatchQueue.main.async { self.isRecording = false }
        #if DEBUG
        print("[MIC] Recording stopped")
        #endif
    }

    // MARK: - Speech recognition

    /// Returns false if on-device model is not yet available (still downloading).
    var isOnDeviceAvailable: Bool {
        SFSpeechRecognizer(locale: Locale(identifier: "en-US"))?.supportsOnDeviceRecognition ?? false
    }

    func startSpeechRecognition(onResult: @escaping (String, Bool) -> Void) {
        onSpeechResult = onResult
        guard SFSpeechRecognizer.authorizationStatus() == .authorized else {
            #if DEBUG
            print("[SPEECH] Not authorized")
            #endif
            return
        }
        guard isOnDeviceAvailable else {
            #if DEBUG
            print("[SPEECH] On-device model not yet available")
            #endif
            return
        }
        startRecognitionTask()
    }

    func stopSpeechRecognition() {
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionRequest = nil
        recognitionTask = nil
        onSpeechResult = nil
        #if DEBUG
        print("[SPEECH] Recognition stopped")
        #endif
    }

    private func startRecognitionTask() {
        guard let format = inputFormat else { return }

        let rec = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
        rec?.defaultTaskHint = .dictation
        recognizer = rec

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.addsPunctuation = true
        request.requiresOnDeviceRecognition = true
        recognitionRequest = request

        recognitionTask = rec?.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }

            if let result {
                let text = result.bestTranscription.formattedString
                let isFinal = result.isFinal
                DispatchQueue.main.async {
                    self.onSpeechResult?(text, isFinal)
                }
                // Apple caps continuous sessions at ~1 min; restart on completion
                if isFinal {
                    self.restartRecognitionTask()
                }
            } else if error != nil {
                // Task ended (timeout/error) — restart if still recording
                if self.isRecording {
                    self.restartRecognitionTask()
                }
            }
        }

        _ = format // silence unused warning; format is used to install the tap in start()
        #if DEBUG
        print("[SPEECH] Recognition task started")
        #endif
    }

    private func restartRecognitionTask() {
        guard isRecording, onSpeechResult != nil else { return }
        recognitionRequest?.endAudio()
        recognitionRequest = nil
        recognitionTask = nil
        #if DEBUG
        print("[SPEECH] Restarting recognition task")
        #endif
        startRecognitionTask()
    }
}
