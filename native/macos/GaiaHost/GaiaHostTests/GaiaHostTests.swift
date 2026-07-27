import XCTest
@testable import Gaia

final class GaiaHostTests: XCTestCase {
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
