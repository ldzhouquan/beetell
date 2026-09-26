import SwiftUI
import AVFoundation
import WatchConnectivity
import WatchKit

@MainActor final class BeeWatchModel: NSObject, ObservableObject, WCSessionDelegate {
    @Published var status = "Pair on iPhone"
    @Published var transcript = ""
    @Published var reply = ""
    @Published var speaking = false
    @Published var ttsEnabled = true
    @Published var meetings: [BeeMeeting] = []
    @Published var minutes = ""
    let recording = BeeMeetingRecorder()
    private let speech = AVSpeechSynthesizer()
    private let audio = BeePCMInput()
    private var gateway: BeeGateway?
    private var socket: URLSessionWebSocketTask?
    private var receiver: Task<Void, Never>?
    private var sender: Task<Void, Never>?
    private var requested = false
    private var ended = false
    private var generation = 0
    private var spoken = ""

    override init() {
        super.init()
        if let config = BeeCredentials.load() { install(config) }
        if WCSession.isSupported() {
            WCSession.default.delegate = self
            WCSession.default.activate()
        }
        speech.usesApplicationAudioSession = false
        WKInterfaceDevice.current().isBatteryMonitoringEnabled = true
    }
    private func install(_ config: BeeConfig) {
        gateway = BeeGateway(config)
        recording.configure(BeeGateway(config))
        status = "Paired: \(config.name)"
    }
    nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        let data = session.receivedApplicationContext["config"] as? Data
        Task { @MainActor in if let data { self.accept(data) } }
    }
    nonisolated func session(_ session: WCSession, didReceiveApplicationContext context: [String: Any]) {
        let data = context["config"] as? Data
        Task { @MainActor in if let data { self.accept(data) } }
    }
    private func accept(_ data: Data) {
        guard let config = try? JSONDecoder().decode(BeeConfig.self, from: data) else { return }
        Task {
            do {
                try await BeeGateway(config).health()
                try BeeCredentials.save(config)
                install(config)
            } catch { status = "Pairing failed: \(error.localizedDescription)" }
        }
    }
    func diagnose() async {
        guard let gateway else { status = "Pair on iPhone"; return }
        do { try await gateway.health(); status = "Gateway reachable" }
        catch { status = "Offline: \(error.localizedDescription)" }
    }
    // Long press begins as soon as the gesture fires; release ends without a PTT replay queue.
    func press() {
        guard !requested, let gateway, !recording.recording else { return }
        requested = true; ended = false; generation += 1
        let current = generation
        status = "Connecting…"; transcript = ""; reply = ""; spoken = ""
        Task {
            do {
                let id = try await gateway.createSession()
                guard generation == current, requested else { return }
                let ws = gateway.socket(); socket = ws
                try await gateway.send(BeeFrame(type: "auth", token: gateway.config.token, v: 1), on: ws)
                try await gateway.send(BeeFrame(type: "audio.start", session_id: id, fmt: "pcm16-16000-mono"), on: ws)
                guard generation == current, requested else { ws.cancel(); return }
                let frames = try audio.start()
                speaking = true; status = "Listening…"
                sender = Task {
                    do {
                        var seq = 0
                        for await frame in frames {
                            try await gateway.send(BeeFrame(type: "audio.chunk", seq: seq,
                                                           pcm_b64: frame.base64EncodedString()), on: ws)
                            seq += 1
                        }
                        if generation == current, ended {
                            try await gateway.send(BeeFrame(type: "audio.end"), on: ws)
                            status = "Transcribing…"
                        }
                    } catch { if generation == current { disconnect() } }
                }
                receiver = Task {
                    do {
                        while generation == current {
                            let frame = try await gateway.receive(on: ws)
                            switch frame.type {
                            case "asr.partial", "asr.final": transcript = frame.text ?? ""
                            case "agent.delta":
                                let delta = frame.text ?? ""
                                reply += delta; spoken += delta
                                if ttsEnabled && (spoken.contains("。") || spoken.contains(".") || spoken.count > 80) {
                                    speech.speak(AVSpeechUtterance(string: spoken)); spoken = ""
                                }
                            case "agent.done":
                                if reply.isEmpty { reply = frame.text ?? ""; spoken = reply }
                                if ttsEnabled && !spoken.isEmpty { speech.speak(AVSpeechUtterance(string: spoken)) }
                                spoken = ""; status = "Ready"; ws.cancel(with: .normalClosure, reason: nil)
                                return
                            case "error": throw BeeError.response(frame.code ?? 5000)
                            default: break // tool/tts.chunk are not enabled in this client
                            }
                        }
                    } catch { if generation == current { disconnect() } }
                }
            } catch { if generation == current { disconnect() } }
        }
    }
    func release() {
        requested = false
        guard speaking else { generation += 1; return }
        ended = true
        speaking = false
        audio.stop() // finishes AsyncStream; sender drains queued frames then sends audio.end
    }
    private func disconnect() {
        generation += 1; requested = false; ended = false
        if speaking { audio.stop(); speaking = false }
        sender?.cancel(); receiver?.cancel()
        socket?.cancel(with: .goingAway, reason: nil); socket = nil
        speech.stopSpeaking(at: .immediate)
        status = "Connection lost — say it again"
        WKInterfaceDevice.current().play(.failure)
    }
    func refreshMeetings() async {
        guard let gateway else { return }
        do { meetings = try await gateway.meetings() }
        catch { status = error.localizedDescription }
    }
    func openMinutes(_ id: String) async {
        guard let gateway else { return }
        do { minutes = try await gateway.minutes(id) }
        catch { minutes = error.localizedDescription }
    }
}

@main struct BeeTellWatchApp: App {
    @StateObject private var model = BeeWatchModel()
    var body: some Scene {
        WindowGroup {
            NavigationStack {
                ScrollView {
                    VStack(spacing: 10) {
                        Text(model.status).font(.caption)
                        Text(model.recording.recording ? "🔴 Recording" : "BeeTell")
                        Text("Hold to talk").font(.caption)
                        RoundedRectangle(cornerRadius: 30).fill(model.speaking ? .orange : .yellow)
                            .frame(height: 70).overlay(Image(systemName: "mic.fill").font(.title).foregroundStyle(.black))
                            .onLongPressGesture(minimumDuration: 0.15, maximumDistance: 100,
                                                pressing: { pressed in
                                if pressed { model.press() } else { model.release() }
                            }, perform: {})
                        if !model.transcript.isEmpty { Text("You: \(model.transcript)") }
                        if !model.reply.isEmpty { Text(model.reply) }
                        Toggle("Speak reply", isOn: $model.ttsEnabled)
                        NavigationLink("Meeting") {
                            BeeMeetingView(recorder: model.recording)
                        }
                        NavigationLink("Minutes") {
                            List(model.meetings) { meeting in
                                NavigationLink(meeting.id) {
                                    ScrollView { Text(model.minutes).font(.caption) }
                                        .task { await model.openMinutes(meeting.id) }
                                }
                            }.task { await model.refreshMeetings() }
                        }
                        Button("Check gateway") { Task { await model.diagnose() } }
                    }.padding(6)
                }
            }
        }
    }
}

struct BeeMeetingView: View {
    @ObservedObject var recorder: BeeMeetingRecorder
    var body: some View {
        VStack {
            Text(recorder.recording ? "🔴 Recording" : recorder.status).font(.caption)
            Text("Chunks: \(recorder.chunkCount)")
            Button(recorder.recording ? "Stop" : "Start") {
                if recorder.recording { recorder.stop() }
                else { Task { await recorder.start() } }
            }
            Button("Mark important") { recorder.mark() }.disabled(!recorder.recording)
            if recorder.hasInterrupted {
                Button("Finalize saved chunks only") { recorder.finalizeInterrupted() }
                    .disabled(recorder.recording)
                Text("Interrupted: final audio may be missing.").font(.caption2)
            }
            Text("Tap Mark for each important point.").font(.caption2)
        }
    }
}
