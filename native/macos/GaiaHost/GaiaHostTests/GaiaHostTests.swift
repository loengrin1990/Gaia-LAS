import XCTest
@testable import Gaia

final class GaiaHostTests: XCTestCase {
    private func temporaryDirectory() throws -> URL {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("GaiaHostTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        return directory
    }

    private func executableFixture(named name: String, script: String, in directory: URL) throws -> URL {
        let executable = directory.appendingPathComponent(name)
        try script.write(to: executable, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
        return executable
    }

    private func repository(in directory: URL, pythonScript: String? = nil) throws -> URL {
        let repository = directory.appendingPathComponent("repository", isDirectory: true)
        try FileManager.default.createDirectory(at: repository, withIntermediateDirectories: true)
        if let pythonScript {
            let venvBin = repository.appendingPathComponent(".venv/bin", isDirectory: true)
            try FileManager.default.createDirectory(at: venvBin, withIntermediateDirectories: true)
            _ = try executableFixture(named: "python3", script: pythonScript, in: venvBin)
        }
        return repository
    }

    private var probeArgumentsCheck: String {
        "[ \"$1\" = \"-c\" ] && [ \"$2\" = \"import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)\" ] || exit 2"
    }

    private var supportedPythonScript: String { "#!/bin/sh\n\(probeArgumentsCheck)\nexit 0\n" }
    private var unsupportedPythonScript: String { "#!/bin/sh\n\(probeArgumentsCheck)\nexit 1\n" }

    func testOriginAcceptsOnlyLiteralGaiaLoopback() throws {
        XCTAssertNotNil(GaiaOrigin.from(url: URL(string: "http://127.0.0.1:8787")!))
        XCTAssertNil(GaiaOrigin.from(url: URL(string: "http://localhost:8787")!))
        XCTAssertNil(GaiaOrigin.from(url: URL(string: "https://127.0.0.1:8787")!))
    }

    func testNavigationAllowsOnlyExactOrigin() throws {
        let policy = NavigationPolicy(origin: try GaiaOrigin(port: 8787))
        XCTAssertTrue(policy.allows(URL(string: "http://127.0.0.1:8787/api/runtime")!))
        XCTAssertFalse(policy.allows(URL(string: "http://127.0.0.1:8788/")!))
        XCTAssertFalse(policy.allows(URL(string: "https://example.com/")!))
        XCTAssertFalse(policy.allows(URL(string: "file:///tmp/material.txt")!))
    }

    func testExplicitBackendConfigurationIsAccepted() throws {
        let locator = BackendLocator(environment: ["GAIA_BACKEND_URL": "http://127.0.0.1:9876"], bundleURL: URL(fileURLWithPath: "/tmp/Gaia.app"))
        XCTAssertEqual(try locator.configuredOrigin(in: URL(fileURLWithPath: "/not-used")), try GaiaOrigin(port: 9876))
    }

    func testPythonExecutableAcceptsExplicitSupportedPython() throws {
        let directory = try temporaryDirectory()
        let explicit = try executableFixture(named: "python", script: supportedPythonScript, in: directory)
        let locator = BackendLocator(environment: ["GAIA_PYTHON": explicit.path])

        XCTAssertEqual(try locator.pythonExecutable(in: try repository(in: directory)), explicit)
    }

    func testPythonExecutableRejectsMissingExplicitPythonWithoutFallback() throws {
        let directory = try temporaryDirectory()
        let repository = try repository(in: directory, pythonScript: supportedPythonScript)
        let locator = BackendLocator(environment: ["GAIA_PYTHON": directory.appendingPathComponent("missing-python").path])

        XCTAssertThrowsError(try locator.pythonExecutable(in: repository)) { XCTAssertEqual($0 as? GaiaHostError, .invalidExplicitPython) }
    }

    func testPythonExecutableRejectsNonExecutableExplicitPythonWithoutFallback() throws {
        let directory = try temporaryDirectory()
        let explicit = try executableFixture(named: "python", script: supportedPythonScript, in: directory)
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: explicit.path)
        let locator = BackendLocator(environment: ["GAIA_PYTHON": explicit.path])

        XCTAssertThrowsError(try locator.pythonExecutable(in: try repository(in: directory, pythonScript: supportedPythonScript))) { XCTAssertEqual($0 as? GaiaHostError, .invalidExplicitPython) }
    }

    func testPythonExecutableRejectsExplicitPython310WithoutFallback() throws {
        let directory = try temporaryDirectory()
        let explicit = try executableFixture(named: "python3.10", script: unsupportedPythonScript, in: directory)
        let locator = BackendLocator(environment: ["GAIA_PYTHON": explicit.path])

        XCTAssertThrowsError(try locator.pythonExecutable(in: try repository(in: directory, pythonScript: supportedPythonScript))) { XCTAssertEqual($0 as? GaiaHostError, .invalidExplicitPython) }
    }

    func testPythonExecutableAcceptsSupportedRepositoryVenv() throws {
        let directory = try temporaryDirectory()
        let repository = try repository(in: directory, pythonScript: supportedPythonScript)

        XCTAssertEqual(try BackendLocator(environment: [:]).pythonExecutable(in: repository), repository.appendingPathComponent(".venv/bin/python3"))
    }

    func testPythonExecutableRejectsUnsupportedRepositoryVenv() throws {
        let directory = try temporaryDirectory()
        let repository = try repository(in: directory, pythonScript: unsupportedPythonScript)

        XCTAssertThrowsError(try BackendLocator(environment: [:]).pythonExecutable(in: repository)) { XCTAssertEqual($0 as? GaiaHostError, .unsupportedRepositoryPython) }
    }

    func testPythonExecutableRejectsMissingRepositoryVenv() throws {
        let directory = try temporaryDirectory()

        XCTAssertThrowsError(try BackendLocator(environment: [:]).pythonExecutable(in: try repository(in: directory))) { XCTAssertEqual($0 as? GaiaHostError, .repositoryPythonNotFound) }
    }

    func testPythonExecutableNeverUsesSystemPythonImplicitly() throws {
        let directory = try temporaryDirectory()

        XCTAssertThrowsError(try BackendLocator(environment: [:]).pythonExecutable(in: try repository(in: directory))) { XCTAssertEqual($0 as? GaiaHostError, .repositoryPythonNotFound) }
    }

    func testPythonExecutableFailsClosedWhenProbeHangs() throws {
        let directory = try temporaryDirectory()
        let hanging = try executableFixture(named: "python", script: "#!/bin/sh\nsleep 10\n", in: directory)
        let start = Date()

        XCTAssertThrowsError(try BackendLocator(environment: ["GAIA_PYTHON": hanging.path]).pythonExecutable(in: try repository(in: directory))) { XCTAssertEqual($0 as? GaiaHostError, .invalidExplicitPython) }
        XCTAssertLessThan(Date().timeIntervalSince(start), 2)
    }

    func testOwnershipStatesAreDistinct() {
        XCTAssertNotEqual(BackendOwnership.attached, BackendOwnership.owned)
    }

    func testDiagnosticsAllowlistExcludesSensitiveKeys() {
        let source = try! String(contentsOf: URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("GaiaHost/BackendSupport.swift"))
        XCTAssertTrue(source.contains("selected_url_count"))
        XCTAssertFalse(source.contains("file_name"))
        XCTAssertFalse(source.contains("file_path"))
    }
}
