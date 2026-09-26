import Foundation

@main struct GatewaySmoke {
    static func main() throws {
        func check(_ address: String, valid: Bool) {
            let parsed = URL(string: address).flatMap { try? BeeConfig.parse($0) }
            precondition((parsed != nil) == valid, "pairing validation: \(address)")
        }
        check("beetell://100.64.1.2:8765?t=secret&n=BeeTell", valid: true)
        check("beetell://100.64.1.2.attacker.com:8765?t=secret", valid: false)
        check("beetell://8.8.8.8:8765?t=secret", valid: false)
        let frame = BeeFrame(type: "auth", token: "secret", v: 1)
        let json = try JSONEncoder().encode(frame)
        precondition(String(data: json, encoding: .utf8) != nil)
        let list = Data("[{\"id\":\"id\",\"state\":\"ready\",\"created\":1.0,\"ready\":true}]".utf8)
        let meetings = try JSONDecoder().decode([BeeMeeting].self, from: list)
        precondition(meetings.first?.ready == true)
        print("Shared gateway smoke passed")
    }
}
