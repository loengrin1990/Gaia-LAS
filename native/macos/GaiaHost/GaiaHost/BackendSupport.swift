import Foundation
import Darwin

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
    case invalidExplicitPython
    case repositoryPythonNotFound
    case unsupportedRepositoryPython
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
        if let explicit = environment["GAIA_PYTHON"] {
            let candidate = URL(fileURLWithPath: explicit)
            guard FileManager.default.isExecutableFile(atPath: candidate.path), supportsPython311(candidate) else {
                throw GaiaHostError.invalidExplicitPython
            }
            return candidate
        }
        let venv = repository.appendingPathComponent(".venv/bin/python3")
        guard FileManager.default.isExecutableFile(atPath: venv.path) else { throw GaiaHostError.repositoryPythonNotFound }
        guard supportsPython311(venv) else { throw GaiaHostError.unsupportedRepositoryPython }
        return venv
    }

    private func supportsPython311(_ executable: URL) -> Bool {
        let source = "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
        guard let probe = IsolatedProbe(executable: executable.path, arguments: ["-c", source]) else { return false }
        return probe.run(timeout: 1) == 0
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

private final class IsolatedProbe {
    private enum WaitResult { case exited(Int32), pending, failed }

    private let pid: pid_t

    init?(executable: String, arguments: [String]) {
        var actions: posix_spawn_file_actions_t? = nil
        var attributes: posix_spawnattr_t? = nil
        guard posix_spawn_file_actions_init(&actions) == 0 else { return nil }
        defer { posix_spawn_file_actions_destroy(&actions) }
        guard posix_spawnattr_init(&attributes) == 0 else { return nil }
        defer { posix_spawnattr_destroy(&attributes) }
        guard posix_spawn_file_actions_addopen(&actions, STDOUT_FILENO, "/dev/null", O_WRONLY, 0) == 0,
              posix_spawn_file_actions_addopen(&actions, STDERR_FILENO, "/dev/null", O_WRONLY, 0) == 0,
              posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETPGROUP)) == 0,
              posix_spawnattr_setpgroup(&attributes, 0) == 0 else { return nil }

        var child: pid_t = 0
        let result = executable.withCString { executablePath in
            arguments[0].withCString { firstArgument in
                arguments[1].withCString { secondArgument in
                    var argv: [UnsafeMutablePointer<CChar>?] = [UnsafeMutablePointer(mutating: executablePath), UnsafeMutablePointer(mutating: firstArgument), UnsafeMutablePointer(mutating: secondArgument), nil]
                    return posix_spawn(&child, executablePath, &actions, &attributes, &argv, environ)
                }
            }
        }
        guard result == 0, child > 0 else { return nil }
        pid = child
    }

    func run(timeout: TimeInterval) -> Int32? {
        switch waitForExit(timeout: timeout) {
        case let .exited(status):
            guard !processGroupExists() else {
                _ = cleanupManagedProcessGroup(directChildAlreadyReaped: true)
                return nil
            }
            return exitStatus(status)
        case .failed:
            _ = cleanupManagedProcessGroup(directChildAlreadyReaped: false)
            return nil
        case .pending:
            _ = cleanupManagedProcessGroup(directChildAlreadyReaped: false)
            return nil
        }
    }

    private func waitForExit(timeout: TimeInterval) -> WaitResult {
        let deadline = Date().addingTimeInterval(timeout)
        var status: Int32 = 0
        repeat {
            let result = waitpid(pid, &status, WNOHANG)
            if result == pid { return .exited(status) }
            if result == -1 {
                if errno == EINTR { continue }
                return .failed
            }
            Thread.sleep(forTimeInterval: 0.01)
        } while Date() < deadline
        return .pending
    }

    private func exitStatus(_ status: Int32) -> Int32? {
        status & 0x7f == 0 ? (status >> 8) & 0xff : nil
    }

    private func cleanupManagedProcessGroup(directChildAlreadyReaped: Bool) -> Bool {
        _ = kill(-pid, SIGTERM)
        var directChildReaped = directChildAlreadyReaped
        if !directChildReaped, case .exited = waitForExit(timeout: 0.2) { directChildReaped = true }
        if !waitForProcessGroupExit(timeout: 0.2) { _ = kill(-pid, SIGKILL) }
        if !directChildReaped, case .exited = waitForExit(timeout: 1) { directChildReaped = true }
        let groupGone = waitForProcessGroupExit(timeout: 1)
        return directChildReaped && groupGone
    }

    private func waitForProcessGroupExit(timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        repeat {
            if !processGroupExists() { return true }
            Thread.sleep(forTimeInterval: 0.01)
        } while Date() < deadline
        return !processGroupExists()
    }

    private func processGroupExists() -> Bool {
        if kill(-pid, 0) == 0 { return true }
        return errno == EPERM
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
        let permitted = Set(["correlation_id", "owned", "port", "http_status", "result", "panel_result", "selected_url_count", "completion_call_count", "error_code", "duration_ms", "main_thread", "allows_multiple_selection", "allows_directories", "event_is_trusted", "input_connected", "input_disabled", "file_count", "upload_started"])
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
