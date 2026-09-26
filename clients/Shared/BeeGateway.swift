import Foundation
import Security

struct BeeConfig: Codable {
    let baseURL: URL
    let token: String
    let name: String

    static func parse(_ url: URL) throws -> BeeConfig {
        guard url.scheme == "beetell", url.user == nil, url.password == nil,
              url.fragment == nil, url.path.isEmpty,
              let host = url.host, !host.isEmpty,
              let port = url.port, (1...65535).contains(port),
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let token = components.queryItems?.first(where: { $0.name == "t" })?.value,
              !token.isEmpty else { throw BeeError.invalidPairing }
        let name = components.queryItems?.first(where: { $0.name == "n" })?.value ?? "BeeTell"
        // HTTP is only suitable over a trusted/encrypted overlay (Tailscale preferred).
        // Do not send bearer credentials to a QR-provided public Internet host.
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4, parts.allSatisfy({ !$0.isEmpty && $0.allSatisfy(\.isNumber) }),
              let a = UInt8(parts[0]), let b = UInt8(parts[1]),
              UInt8(parts[2]) != nil, UInt8(parts[3]) != nil,
              (a == 10 || (a == 172 && (16...31).contains(b)) ||
               (a == 192 && b == 168) || (a == 100 && (64...127).contains(b))) else {
            throw BeeError.invalidPairing
        }
        guard let base = URL(string: "http://\(host.contains(":") ? "[\(host)]" : host):\(port)") else {
            throw BeeError.invalidPairing
        }
        return BeeConfig(baseURL: base, token: token, name: name)
    }
}

enum BeeError: LocalizedError {
    case invalidPairing, unpaired, response(Int), malformed, disconnected
    var errorDescription: String? {
        switch self {
        case .invalidPairing: return "Invalid pairing QR code"
        case .unpaired: return "Pair on iPhone first"
        case .response(let code): return "Gateway returned HTTP \(code)"
        case .malformed: return "Unexpected gateway response"
        case .disconnected: return "Connection lost — say it again"
        }
    }
}

// No token in UserDefaults, logs or transfer files. Each target has its own Keychain.
enum BeeCredentials {
    private static let service = "com.beetell.gateway"
    static func save(_ config: BeeConfig) throws {
        let data = try JSONEncoder().encode(config)
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service, kSecAttrAccount as String: "pairing"]
        SecItemDelete(query as CFDictionary)
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw BeeError.malformed }
    }
    static func load() -> BeeConfig? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service, kSecAttrAccount as String: "pairing",
                                    kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(BeeConfig.self, from: data)
    }
}

struct BeeMeeting: Decodable, Identifiable {
    let id: String
    let state: String
    let created: Double
    let ready: Bool
}

struct BeeFrame: Codable {
    let type: String
    var token: String? = nil
    var v: Int? = nil
    var session_id: String? = nil
    var fmt: String? = nil
    var seq: Int? = nil
    var pcm_b64: String? = nil
    var text: String? = nil
    var meeting_id: String? = nil
    var ts: Double? = nil
    var label: String? = nil
    var scope: String? = nil
    var code: Int? = nil
    var message: String? = nil
}

struct BeeID: Decodable {
    let session_id: String?
    let meeting_id: String?
}

final class BeeGateway {
    let config: BeeConfig
    private let session: URLSession
    init(_ config: BeeConfig) {
        self.config = config
        let settings = URLSessionConfiguration.default
        settings.timeoutIntervalForRequest = 20
        self.session = URLSession(configuration: settings)
    }
    private func url(_ path: String) -> URL {
        let pieces = path.split(separator: "?", maxSplits: 1).map(String.init)
        var components = URLComponents(url: config.baseURL, resolvingAgainstBaseURL: false)!
        components.path = "/" + pieces[0]
        if pieces.count == 2 { components.percentEncodedQuery = pieces[1] }
        return components.url!
    }
    private func safeID(_ id: String) throws -> String {
        guard id.range(of: "^[A-Za-z0-9_-]+$", options: .regularExpression) != nil else { throw BeeError.malformed }
        return id
    }
    private func request(_ path: String, method: String = "GET", body: Data? = nil,
                         contentType: String? = nil, authenticated: Bool = true) async throws -> Data {
        var req = URLRequest(url: url(path))
        req.httpMethod = method
        req.httpBody = body
        if authenticated { req.setValue("Bearer \(config.token)", forHTTPHeaderField: "Authorization") }
        if let contentType { req.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw BeeError.malformed }
        guard (200..<300).contains(http.statusCode) else { throw BeeError.response(http.statusCode) }
        return data
    }
    func health() async throws {
        _ = try await request("healthz", authenticated: false)
    }
    func agents() async throws -> Data { try await request("v1/agents") }
    func createSession() async throws -> String {
        let data = try await request("v1/sessions", method: "POST", body: Data("{}".utf8), contentType: "application/json")
        guard let id = try JSONDecoder().decode(BeeID.self, from: data).session_id else { throw BeeError.malformed }
        return try safeID(id)
    }
    func createMeeting() async throws -> String {
        let data = try await request("v1/meetings", method: "POST", body: Data("{}".utf8), contentType: "application/json")
        guard let id = try JSONDecoder().decode(BeeID.self, from: data).meeting_id else { throw BeeError.malformed }
        return try safeID(id)
    }
    func meetings() async throws -> [BeeMeeting] {
        try JSONDecoder().decode([BeeMeeting].self, from: await request("v1/meetings"))
    }
    func minutes(_ id: String) async throws -> String {
        let id = try safeID(id)
        guard let text = String(data: try await request("v1/meetings/\(id)/minutes"), encoding: .utf8) else { throw BeeError.malformed }
        return text
    }
    func finalize(_ id: String, expectedChunks: Int) async throws {
        let id = try safeID(id)
        let payload = try JSONSerialization.data(withJSONObject: ["expected_chunks": expectedChunks])
        _ = try await request("v1/meetings/\(id)/finalize", method: "POST", body: payload,
                              contentType: "application/json")
    }
    func upload(_ id: String, seq: Int, file: URL) async throws {
        let id = try safeID(id)
        guard seq >= 0 else { throw BeeError.malformed }
        _ = try await request("v1/meetings/\(id)/chunks?seq=\(seq)", method: "POST",
                              body: try Data(contentsOf: file), contentType: "audio/mp4")
    }
    func socket() -> URLSessionWebSocketTask {
        var components = URLComponents(url: url("v1/ws"), resolvingAgainstBaseURL: false)!
        components.scheme = config.baseURL.scheme == "https" ? "wss" : "ws"
        let task = session.webSocketTask(with: components.url!)
        task.resume()
        return task
    }
    func send(_ frame: BeeFrame, on socket: URLSessionWebSocketTask) async throws {
        let data = try JSONEncoder().encode(frame)
        guard let text = String(data: data, encoding: .utf8) else { throw BeeError.malformed }
        try await socket.send(.string(text))
    }
    func receive(on socket: URLSessionWebSocketTask) async throws -> BeeFrame {
        let message = try await socket.receive()
        let data: Data
        switch message {
        case .data(let bytes): data = bytes
        case .string(let text): data = Data(text.utf8)
        @unknown default: throw BeeError.malformed
        }
        return try JSONDecoder().decode(BeeFrame.self, from: data)
    }
    func mark(_ id: String, ts: Double, label: String) async throws {
        let id = try safeID(id)
        let payload = try JSONSerialization.data(withJSONObject: ["ts": ts, "label": label])
        _ = try await request("v1/meetings/\(id)/marks", method: "POST",
                              body: payload, contentType: "application/json")
    }
}
