import AVFoundation
import Foundation
import WatchKit
import SwiftUI
import Network

struct BeePendingChunk: Codable {
    let meetingID: String
    let seq: Int
    let filename: String
    var retry: Int = 0
}
struct BeePendingMarker: Codable {
    let meetingID: String
    let seconds: Double
    let label: String
}
struct BeeMeetingJob: Codable {
    let id: String
    var nextSeq: Int = 0
    var finished = false
    var finalized = false
    var interrupted = false
    var chunks: [BeePendingChunk] = []
    var markers: [BeePendingMarker] = []
}

// Manifest is atomically replaced BEFORE upload; uploaded files are removed only after 2xx.
@MainActor final class BeeMeetingRecorder: NSObject, ObservableObject {
    @Published private(set) var recording = false
    @Published private(set) var status = "Ready"
    @Published private(set) var chunkCount = 0
    private var jobs: [BeeMeetingJob] = []
    var hasInterrupted: Bool { jobs.contains { $0.interrupted } }
    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private var started: Date?
    private var gateway: BeeGateway?
    private var uploading = false
    private var lastRetry = Date.distantPast
    private let network = NWPathMonitor()
    private let root: URL

    override init() {
        root = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("MeetingQueue", isDirectory: true)
        super.init()
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        if let data = try? Data(contentsOf: root.appendingPathComponent("manifest.json")),
           let saved = try? JSONDecoder().decode([BeeMeetingJob].self, from: data) {
            // Only an explicitly stopped job may auto-finalize; a killed recorder's open
            // AAC file might be incomplete and must not silently become a complete meeting.
            jobs = saved.map { job in
                var recovered = job
                if !recovered.finalized && !recovered.finished { recovered.interrupted = true }
                return recovered
            }
            if hasInterrupted { status = "Recording interrupted — last chunk may be missing" }
        }
        network.pathUpdateHandler = { [weak self] path in
            guard path.status == .satisfied else { return }
            Task { @MainActor in await self?.drain() }
        }
        network.start(queue: DispatchQueue(label: "com.beetell.watch.network"))
    }
    private func persist() throws {
        let data = try JSONEncoder().encode(jobs)
        try data.write(to: root.appendingPathComponent("manifest.json"), options: .atomic)
    }
    func configure(_ gateway: BeeGateway) {
        self.gateway = gateway
        Task { await drain() }
    }
    func start() async {
        guard !recording, let gateway else { status = "Pair first"; return }
        guard WKInterfaceDevice.current().batteryLevel < 0 || WKInterfaceDevice.current().batteryLevel >= 0.20 else {
            status = "Battery below 20% — charge before recording"; return
        }
        do {
            let id = try await gateway.createMeeting()
            jobs.append(BeeMeetingJob(id: id))
            try persist()
            started = Date()
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement)
            try session.setActive(true)
            try beginChunk()
            chunkCount = 0
            recording = true
            status = "Recording • inform participants"
            WKInterfaceDevice.current().play(.start)
            // watchOS must grant microphone access on device; background survival is not guaranteed.
            timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                Task { @MainActor in self?.tick() }
            }
        } catch { status = error.localizedDescription }
    }
    private func beginChunk() throws {
        guard let job = jobs.last else { return }
        let path = root.appendingPathComponent("\(job.id)-\(job.nextSeq).m4a")
        let settings: [String: Any] = [AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
                                        AVSampleRateKey: 16_000.0, AVNumberOfChannelsKey: 1,
                                        AVEncoderBitRateKey: 32_000]
        let recorder = try AVAudioRecorder(url: path, settings: settings)
        guard recorder.record() else { throw BeeError.malformed }
        self.recorder = recorder
    }
    private func tick() {
        guard recording else { return }
        let battery = WKInterfaceDevice.current().batteryLevel
        if battery >= 0 && battery < 0.20 {
            WKInterfaceDevice.current().play(.failure)
            stop()
            status = "Low battery — stopped; charge and start a new segment"
            return
        }
        if (recorder?.currentTime ?? 0) >= 30 { rotate() }
        // Periodically retry while connected.
        if Date().timeIntervalSince(lastRetry) >= 10 {
            lastRetry = Date()
            Task { await drain() }
        }
    }
    private func seal() throws {
        guard let recorder, !jobs.isEmpty else { return }
        recorder.stop()
        self.recorder = nil
        let file = recorder.url
        if let size = (try? file.resourceValues(forKeys: [.fileSizeKey]))?.fileSize, size > 0 {
            let index = jobs.count - 1
            jobs[index].chunks.append(BeePendingChunk(meetingID: jobs[index].id,
                                                        seq: jobs[index].nextSeq, filename: file.lastPathComponent))
            jobs[index].nextSeq += 1
            chunkCount = jobs[index].nextSeq
            try persist()
        } else { try? FileManager.default.removeItem(at: file) }
    }
    private func rotate() {
        do {
            try seal()
            try beginChunk()
            Task { await drain() }
        } catch { status = "Recording stopped: \(error.localizedDescription)"; stop() }
    }
    func stop() {
        guard recording else { return }
        timer?.invalidate(); timer = nil
        recording = false
        defer { try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
        do {
            try seal()
            if !jobs.isEmpty { jobs[jobs.count - 1].finished = true }
            try persist()
            status = "Queued for upload"
            Task { await drain() }
        } catch { status = "Storage error: \(error.localizedDescription)" }
    }
    func mark() {
        guard recording, let started, !jobs.isEmpty else { return }
        let id = jobs[jobs.count - 1].id
        jobs[jobs.count - 1].markers.append(BeePendingMarker(meetingID: id,
                                                 seconds: Date().timeIntervalSince(started), label: "Important"))
        do { try persist(); WKInterfaceDevice.current().play(.click); Task { await drain() } }
        catch { status = "Marker save failed" }
    }
    func finalizeInterrupted() {
        guard !recording else { return }
        for index in jobs.indices where jobs[index].interrupted {
            let job = jobs[index]
            let orphan = root.appendingPathComponent("\(job.id)-\(job.nextSeq).m4a")
            try? FileManager.default.removeItem(at: orphan)
            jobs[index].markers.append(BeePendingMarker(meetingID: job.id, seconds: 0,
                label: "录音中断：最后一片可能缺失（用户确认仅处理已封存片段）"))
            jobs[index].interrupted = false
            jobs[index].finished = true
        }
        do { try persist(); status = "Incomplete recording acknowledged; uploading sealed chunks"; Task { await drain() } }
        catch { status = "Could not save recovery decision" }
    }
    func drain() async {
        guard !uploading, let gateway else { return }
        uploading = true
        defer { uploading = false }
        for index in jobs.indices {
            while !jobs[index].chunks.isEmpty {
                let chunk = jobs[index].chunks[0]
                do {
                    try await gateway.upload(chunk.meetingID, seq: chunk.seq, file: root.appendingPathComponent(chunk.filename))
                    let previous = jobs[index]
                    jobs[index].chunks.removeFirst()
                    do { try persist() }
                    catch { jobs[index] = previous; throw error }
                    try? FileManager.default.removeItem(at: root.appendingPathComponent(chunk.filename))
                } catch {
                    if !jobs[index].chunks.isEmpty { jobs[index].chunks[0].retry += 1 }
                    try? persist()
                    status = "Upload pending — retry on next launch/network"
                    return
                }
            }
            while !jobs[index].markers.isEmpty {
                let marker = jobs[index].markers[0]
                do {
                    try await gateway.mark(marker.meetingID, ts: marker.seconds, label: marker.label)
                    jobs[index].markers.removeFirst()
                    try persist()
                } catch { status = "Marker pending"; return }
            }
            if jobs[index].finished && !jobs[index].finalized {
                do {
                    try await gateway.finalize(jobs[index].id, expectedChunks: jobs[index].nextSeq)
                    let previous = jobs[index]
                    jobs[index].finished = false
                    jobs[index].finalized = true
                    do { try persist() }
                    catch { jobs[index] = previous; throw error }
                    status = "Minutes processing"
                } catch { status = "Finalize pending"; return }
            }
        }
        jobs.removeAll { $0.finalized && $0.chunks.isEmpty && $0.markers.isEmpty }
        try? persist()
    }
}
