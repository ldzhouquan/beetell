import AVFoundation
import Foundation

// 200 ms 16 kHz mono s16le, even when hardware input runs at another rate.
final class BeePCMInput {
    private let engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private var remainder = Data()
    private var continuation: AsyncStream<Data>.Continuation?

    func start() throws -> AsyncStream<Data> {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .voiceChat)
        try session.setActive(true)
        let stream = AsyncStream<Data> { continuation in self.continuation = continuation }
        let input = engine.inputNode
        let source = input.outputFormat(forBus: 0)
        guard let target = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000,
                                         channels: 1, interleaved: false),
              let converter = AVAudioConverter(from: source, to: target) else { throw BeeError.malformed }
        self.converter = converter
        input.installTap(onBus: 0, bufferSize: 4096, format: source) { [weak self] buffer, _ in
            self?.consume(buffer, target: target, converter: converter)
        }
        engine.prepare()
        do { try engine.start() }
        catch { input.removeTap(onBus: 0); throw error }
        return stream
    }

    private func consume(_ buffer: AVAudioPCMBuffer, target: AVAudioFormat, converter: AVAudioConverter) {
        let capacity = AVAudioFrameCount(ceil(Double(buffer.frameLength) * 16_000 / buffer.format.sampleRate) + 64)
        guard let output = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return }
        var used = false
        var conversionError: NSError?
        converter.convert(to: output, error: &conversionError) { _, status in
            if used { status.pointee = .noDataNow; return nil }
            used = true
            status.pointee = .haveData
            return buffer
        }
        guard conversionError == nil, let samples = output.floatChannelData?[0] else { return }
        for i in 0..<Int(output.frameLength) {
            let value = Int16(max(-32768, min(32767, Int((samples[i] * 32767).rounded()))))
            remainder.append(UInt8(truncatingIfNeeded: value))
            remainder.append(UInt8(truncatingIfNeeded: value >> 8))
        }
        while remainder.count >= 6400 {
            continuation?.yield(Data(remainder.prefix(6400)))
            remainder.removeFirst(6400)
        }
    }

    func stop() {
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        if !remainder.isEmpty { continuation?.yield(remainder) }
        remainder.removeAll()
        continuation?.finish()
        continuation = nil
        converter = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
