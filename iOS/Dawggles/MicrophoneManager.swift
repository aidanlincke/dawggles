import Foundation
import AVFoundation
import Speech
import Combine

/// Captures microphone audio and runs on-device live speech recognition via SpeechAnalyzer.
/// Start/stop driven by mic_activate / mic_deactivate events from the Pi.
class MicrophoneManager: ObservableObject {
    static let shared = MicrophoneManager()

    @Published private(set) var isRecording = false

    /// Called on the main thread with (text, isFinal) as speech is recognized.
    var onSpeechResult: ((String, Bool) -> Void)?

    private let engine = AVAudioEngine()
    private var nativeFormat: AVAudioFormat?

    private var speechConverter: AVAudioConverter?
    private var speechFormat: AVAudioFormat?
    private var inputBuilder: AsyncStream<AnalyzerInput>.Continuation?
    private var analyzerTask: Task<Void, Never>?

    private init() {}

    func requestPermission() {
        AVAudioApplication.requestRecordPermission { _ in }
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
        let native = inputNode.outputFormat(forBus: 0)
        nativeFormat = native

        inputNode.installTap(onBus: 0, bufferSize: 4096, format: native) { [weak self] buffer, _ in
            guard let self,
                  let builder = self.inputBuilder,
                  let fmt = self.speechFormat,
                  let conv = self.speechConverter else { return }

            let outFrames = AVAudioFrameCount(Double(buffer.frameLength) * fmt.sampleRate / native.sampleRate)
            guard let converted = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: outFrames) else { return }
            var err: NSError?
            conv.convert(to: converted, error: &err) { _, status in
                status.pointee = .haveData
                return buffer
            }
            guard err == nil else { return }
            builder.yield(AnalyzerInput(buffer: converted))
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

    func startSpeechRecognition(locale: Locale, onResult: @escaping (String, Bool) -> Void) {
        onSpeechResult = onResult

        // Create stream before the async task so the tap can start buffering immediately
        let (stream, builder) = AsyncStream<AnalyzerInput>.makeStream()
        inputBuilder = builder

        analyzerTask = Task { [weak self] in
            guard let self, let native = self.nativeFormat else { return }

            let resolved = await SpeechTranscriber.supportedLocale(equivalentTo: locale) ?? locale
            let transcriber = SpeechTranscriber(locale: resolved, preset: .progressiveTranscription)

            guard let analyzerFmt = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
                #if DEBUG
                print("[SPEECH] No compatible audio format available for \(resolved.identifier)")
                #endif
                return
            }

            self.speechFormat = analyzerFmt
            self.speechConverter = AVAudioConverter(from: native, to: analyzerFmt)

            let analyzer = SpeechAnalyzer(modules: [transcriber])
            do {
                try await analyzer.start(inputSequence: stream)
                for try await result in transcriber.results {
                    let text = NSAttributedString(result.text).string
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !text.isEmpty else { continue }
                    let isFinal = result.isFinal
                    await MainActor.run { [weak self] in
                        self?.onSpeechResult?(text, isFinal)
                    }
                }
            } catch {
                #if DEBUG
                print("[SPEECH] \(error)")
                #endif
            }
        }
    }

    func stopSpeechRecognition() {
        inputBuilder?.finish()
        inputBuilder = nil
        analyzerTask?.cancel()
        analyzerTask = nil
        speechConverter = nil
        speechFormat = nil
        onSpeechResult = nil
        #if DEBUG
        print("[SPEECH] Recognition stopped")
        #endif
    }
}
