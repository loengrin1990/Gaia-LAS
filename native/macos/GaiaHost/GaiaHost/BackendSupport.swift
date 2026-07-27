import Foundation

struct GaiaOrigin: Equatable {
    let port: Int

    var url: URL { URL(string: "http://127.0.0.1:\(port)")! }

    init(port: Int) throws {
        guard (1...65535).contains(port) else { throw GaiaHostError.invalidConfiguration }
        self.port = port
    }

    static func from(url: URL) -> GaiaOrigin? {
        guard url.scheme == "http", url.host == "127.0.0.1", let port = url.port else { return nil }
        return try? GaiaOrigin(port: port)
    }
}

enum BackendOwnership: Equatable { case attached, owned }

enum GaiaHostError: Error, Equatable {
    case invalidConfiguration
    case pythonNotFound
    case repositoryNotFound
    case portOccupied
    case backendUnavailable
}

struct BackendRuntime: Decodable {
    let ready: Bool
    let runtime_id: String
    let api_contract_version: Int

    var isGaia: Bool { ready && !runtime_id.isEmpty && api_contract_version >= 1 }
}

struct BackendLocator {
    let environment: [String: String]
    let bundleURL: URL

    init(environment: [String: String] = ProcessInfo.processInfo.environment, bundleURL: URL = Bundle.main.bundleURL) {
        self.environment = environment
        self.bundleURL = bundleURL
    }

    func repositoryRoot() throws -> URL {
        if let explicit = environment["GAIA_REPOSITORY_ROOT"], let root = validatedRepository(URL(fileURLWithPath: explicit)) {
            return root
        }
        var current = bundleURL.standardizedFileURL
        for _ in 0..<10 {
            if let root = validatedRepository(current) { return root }
            current.deleteLastPathComponent()
        }
        throw GaiaHostError.repositoryNotFound
    }

    func pythonExecutable(in repository: URL) throws -> URL {
        if let explicit = environment["GAIA_PYTHON"], FileManager.default.isExecutableFile(atPath: explicit) {
            return URL(fileURLWithPath: explicit)
        }
        let venv = repository.appendingPathComponent(".venv/bin/python3")
        if FileManager.default.isExecutableFile(atPath: venv.path) { return venv }
        let systemPython = URL(fileURLWithPath: "/usr/bin/python3")
        if FileManager.default.isExecutableFile(atPath: systemPython.path) { return systemPython }
        throw GaiaHostError.pythonNotFound
    }

    func configuredOrigin(in repository: URL) throws -> GaiaOrigin {
        if let explicit = environment["GAIA_BACKEND_URL"], let url = URL(string: explicit), let origin = GaiaOrigin.from(url: url) {
            return origin
        }
        let config = repository.appendingPathComponent("config.json")
        let data = try Data(contentsOf: config)
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        let server = object?["server"] as? [String: Any]
        guard server?["host"] as? String == "127.0.0.1", let port = server?["port"] as? Int else {
            throw GaiaHostError.invalidConfiguration
        }
        return try GaiaOrigin(port: port)
    }

    private func validatedRepository(_ root: URL) -> URL? {
        FileManager.default.fileExists(atPath: root.appendingPathComponent("app.py").path) ? root : nil
    }
}

struct NavigationPolicy {
    let origin: GaiaOrigin

    func allows(_ url: URL) -> Bool {
        url.scheme == "http" && url.host == "127.0.0.1" && url.port == origin.port
    }
}

final class NativeDiagnostics {
    private let enabled: Bool
    private let path: String?

    init(environment: [String: String] = ProcessInfo.processInfo.environment) {
        enabled = environment["GAIA_NATIVE_HOST_DIAGNOSTICS"] == "1"
        path = environment["GAIA_NATIVE_HOST_DIAGNOSTICS_PATH"]
    }

    func emit(_ event: String, fields: [String: Any] = [:]) {
        guard enabled, let path else { return }
        let permitted = Set(["correlation_id", "owned", "port", "http_status", "result", "selected_url_count", "completion_call_count", "error_code", "duration_ms"])
        var payload = fields.filter { permitted.contains($0.key) }
        payload["event"] = event
        payload["timestamp"] = ISO8601DateFormatter().string(from: Date())
        guard JSONSerialization.isValidJSONObject(payload), let data = try? JSONSerialization.data(withJSONObject: payload),
              let handle = FileHandle(forWritingAtPath: path) else { return }
        defer { handle.closeFile() }
        handle.seekToEndOfFile()
        handle.write(data)
        handle.write(Data([0x0a]))
    }
}
