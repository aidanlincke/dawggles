import Foundation
import AVFoundation
import WhisperKit
import Combine

/// Captures microphone audio and streams 16 kHz mono Int16 PCM via `onAudioBuffer`.
/// Optionally runs on-device live speech recognition via WhisperKit and delivers
/// results via `onSpeechResult`. Start/stop driven by mic_activate / mic_deactivate.
class MicrophoneManager: ObservableObject {
    static let shared = MicrophoneManager()

    @Published private(set) var isRecording = false
    @Published private(set) var modelState: ModelState = .unloaded

    enum ModelState: Equatable {
        case unloaded
        case loading
        case ready
        case unavailable(String)
    }

    /// Called on a background thread with each chunk of raw PCM data ready to send.
    var onAudioBuffer: ((Data) -> Void)?
    /// Called on the main thread with recognized text when a chunk is transcribed.
    var onSpeechResult: ((String) -> Void)?

    private let engine = AVAudioEngine()
    private let targetSampleRate: Double = 16000
    private let targetChannels: AVAudioChannelCount = 1

    // 5-second chunks at 16 kHz
    private let chunkSampleCount = 16000 * 5
    private var pendingSamples: [Float] = []
    private var isTranscribing = false
    private var whisperPipe: WhisperKit?

    private init() {}

    func requestPermission() {
        AVAudioApplication.requestRecordPermission { _ in }
    }

    // MARK: - Model

    func loadModelIfNeeded() {
        guard case .unloaded = modelState else { return }
        modelState = .loading
        Task {
            do {
                let pipe = try await WhisperKit(model: "base")
                await MainActor.run {
                    self.whisperPipe = pipe
                    self.modelState = .ready
                }
                #if DEBUG
                print("[WHISPER] Model ready")
                #endif
            } catch {
                await MainActor.run {
                    self.modelState = .unavailable(error.localizedDescription)
                }
                #if DEBUG
                print("[WHISPER] Model load failed: \(error)")
                #endif
            }
        }
    }

    var isModelReady: Bool {
        if case .ready = modelState { return true }
        return false
    }

    // MARK: - Recording

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

            let outFrames = AVAudioFrameCount(
                Double(buffer.frameLength) * self.targetSampleRate / nativeFormat.sampleRate
            )
            guard let converted = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: outFrames) else { return }

            var error: NSError?
            converter.convert(to: converted, error: &error) { _, outStatus in
                outStatus.pointee = .haveData
                return buffer
            }
            guard error == nil, let int16Ptr = converted.int16ChannelData else { return }
            let frameCount = Int(converted.frameLength)

            // Stream Int16 PCM to Pi
            let data = Data(bytes: int16Ptr[0], count: frameCount * 2)
            self.onAudioBuffer?(data)

            // Accumulate Float32 samples for WhisperKit
            if self.onSpeechResult != nil {
                let floats = (0..<frameCount).map { Float(int16Ptr[0][$0]) / 32768.0 }
                self.pendingSamples.append(contentsOf: floats)
                self.transcribeIfReady()
            }
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
        onSpeechResult = nil
        pendingSamples = []
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        DispatchQueue.main.async { self.isRecording = false }
        #if DEBUG
        print("[MIC] Recording stopped")
        #endif
    }

    // MARK: - Speech recognition

    func startSpeechRecognition(onResult: @escaping (String) -> Void) {
        guard isModelReady else {
            #if DEBUG
            print("[WHISPER] Model not ready")
            #endif
            return
        }
        pendingSamples = []
        isTranscribing = false
        onSpeechResult = onResult
    }

    func stopSpeechRecognition() {
        onSpeechResult = nil
        pendingSamples = []
        isTranscribing = false
    }

    private func transcribeIfReady() {
        guard !isTranscribing,
              pendingSamples.count >= chunkSampleCount,
              let pipe = whisperPipe,
              let callback = onSpeechResult else { return }

        let chunk = Array(pendingSamples.prefix(chunkSampleCount))
        pendingSamples = Array(pendingSamples.dropFirst(chunkSampleCount))
        isTranscribing = true

        Task {
            defer { self.isTranscribing = false }
            do {
                let options = DecodingOptions(task: .transcribe, language: "en")
                let results = try await pipe.transcribe(audioArray: chunk, decodeOptions: options)
                let text = results.compactMap { $0.text.nilIfEmpty() }.joined(separator: " ")
                if !text.isEmpty {
                    await MainActor.run { callback(text) }
                }
            } catch {
                #if DEBUG
                print("[WHISPER] Transcription error: \(error)")
                #endif
            }
        }
    }
}

private extension String {
    func nilIfEmpty() -> String? {
        let t = trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : t
    }
}
