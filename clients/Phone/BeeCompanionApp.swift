import SwiftUI
import AVFoundation
import WatchConnectivity
import UIKit

@MainActor final class BeePhoneModel: NSObject, ObservableObject, WCSessionDelegate {
    @Published var status = "Scan a gateway QR code"
    @Published var meetings: [BeeMeeting] = []
    @Published var minutes = ""
    @Published var gateway: BeeGateway?
    override init() {
        super.init()
        if let config = BeeCredentials.load() { gateway = BeeGateway(config); status = "Paired: \(config.name)" }
        if WCSession.isSupported() {
            WCSession.default.delegate = self
            WCSession.default.activate()
        }
    }
    nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        Task { @MainActor in self.transfer() }
    }
    nonisolated func sessionDidBecomeInactive(_ session: WCSession) {}
    nonisolated func sessionDidDeactivate(_ session: WCSession) { session.activate() }
    private func transfer() {
        guard WCSession.default.activationState == .activated, let config = BeeCredentials.load() else { return }
        do { try WCSession.default.updateApplicationContext(["config": JSONEncoder().encode(config)]) }
        catch { status = "Saved on iPhone; Watch transfer pending: \(error.localizedDescription)" }
    }
    func pair(_ scanned: String) async {
        do {
            guard let url = URL(string: scanned) else { throw BeeError.invalidPairing }
            let config = try BeeConfig.parse(url)
            try await BeeGateway(config).health()
            try BeeCredentials.save(config)
            gateway = BeeGateway(config)
            status = "Paired: \(config.name)"
            transfer()
            await refresh()
        } catch { status = "Pairing failed: \(error.localizedDescription)" }
    }
    func refresh() async {
        guard let gateway else { return }
        do { meetings = try await gateway.meetings() }
        catch { status = "Minutes unavailable: \(error.localizedDescription)" }
    }
    func open(_ id: String) async {
        guard let gateway else { return }
        do { minutes = try await gateway.minutes(id) }
        catch { minutes = error.localizedDescription }
    }
}

struct BeeQRScanner: UIViewControllerRepresentable {
    let onScan: (String) -> Void
    func makeCoordinator() -> Coordinator { Coordinator(onScan: onScan) }
    func makeUIViewController(context: Context) -> UIViewController {
        let controller = UIViewController()
        let capture = AVCaptureSession()
        guard let camera = AVCaptureDevice.default(for: .video),
              let input = try? AVCaptureDeviceInput(device: camera), capture.canAddInput(input) else { return controller }
        capture.addInput(input)
        let output = AVCaptureMetadataOutput()
        guard capture.canAddOutput(output) else { return controller }
        capture.addOutput(output)
        output.setMetadataObjectsDelegate(context.coordinator, queue: .main)
        output.metadataObjectTypes = [.qr]
        let preview = AVCaptureVideoPreviewLayer(session: capture)
        preview.videoGravity = .resizeAspectFill
        preview.frame = UIScreen.main.bounds
        controller.view.layer.addSublayer(preview)
        context.coordinator.capture = capture
        DispatchQueue.global(qos: .userInitiated).async { capture.startRunning() }
        return controller
    }
    func updateUIViewController(_ controller: UIViewController, context: Context) {}
    static func dismantleUIViewController(_ controller: UIViewController, coordinator: Coordinator) {
        let capture = coordinator.capture
        DispatchQueue.global(qos: .userInitiated).async { capture?.stopRunning() }
    }
    final class Coordinator: NSObject, AVCaptureMetadataOutputObjectsDelegate {
        let onScan: (String) -> Void
        var capture: AVCaptureSession?
        private var completed = false
        init(onScan: @escaping (String) -> Void) { self.onScan = onScan }
        func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject], from connection: AVCaptureConnection) {
            guard !completed, let code = (metadataObjects.first as? AVMetadataMachineReadableCodeObject)?.stringValue else { return }
            completed = true
            onScan(code)
        }
    }
}

@main struct BeeTellCompanionApp: App {
    @StateObject private var model = BeePhoneModel()
    @State private var scanning = false
    var body: some Scene {
        WindowGroup {
            NavigationStack {
                List {
                    Section("Gateway") {
                        Text(model.status)
                        Button("Scan pairing QR") {
                            Task {
                                let allowed = await AVCaptureDevice.requestAccess(for: .video)
                                if allowed { scanning = true }
                                else { model.status = "Camera permission required in Settings" }
                            }
                        }
                    }
                    Section("Minutes") {
                        Button("Refresh") { Task { await model.refresh() } }
                        ForEach(model.meetings) { meeting in
                            NavigationLink(meeting.id) {
                                ScrollView {
                                    VStack(alignment: .leading, spacing: 16) {
                                        // Markdown rendered as attributed text; no editing or audio UI.
                                        Text(.init(model.minutes))
                                            .textSelection(.enabled)
                                        ShareLink(item: model.minutes, subject: Text("BeeTell minutes"))
                                    }.padding()
                                }.navigationTitle("Minutes")
                                    .task { await model.open(meeting.id) }
                            }
                        }
                    }
                }.navigationTitle("BeeTell")
                    .task { await model.refresh() }
                    .sheet(isPresented: $scanning) {
                        BeeQRScanner { code in
                            scanning = false
                            Task { await model.pair(code) }
                        }.ignoresSafeArea()
                    }
                    .onOpenURL { url in Task { await model.pair(url.absoluteString) } }
            }
        }
    }
}
