import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let diagnostics: NativeDiagnostics
    private let correlationID: String
    private var loadingWindow: NSWindow?
    private var hostController: GaiaHostController?

    init(diagnostics: NativeDiagnostics, correlationID: String) {
        self.diagnostics = diagnostics
        self.correlationID = correlationID
        super.init()
    }

    func applicationWillFinishLaunching(_ notification: Notification) {
        diagnostics.emit("application_will_finish_launching", fields: ["correlation_id": correlationID])
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        diagnostics.emit("application_did_finish_launching", fields: ["correlation_id": correlationID])
        let window = makeLoadingWindow()
        loadingWindow = window
        diagnostics.emit("loading_window_created", fields: ["correlation_id": correlationID])
        window.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
        diagnostics.emit("loading_window_shown", fields: ["correlation_id": correlationID])

        let controller = GaiaHostController(window: window, diagnostics: diagnostics, correlationID: correlationID)
        hostController = controller
        diagnostics.emit("backend_coordinator_started", fields: ["correlation_id": correlationID])
        controller.start()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        hostController?.close()
        return .terminateNow
    }

    private func makeLoadingWindow() -> NSWindow {
        let label = NSTextField(labelWithString: "Gaia запускается…")
        label.font = .systemFont(ofSize: 20, weight: .medium)
        label.alignment = .center
        label.translatesAutoresizingMaskIntoConstraints = false
        let content = NSView()
        content.addSubview(label)
        NSLayoutConstraint.activate([
            label.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            label.centerYAnchor.constraint(equalTo: content.centerYAnchor),
        ])
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 720, height: 420), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Gaia"
        window.contentView = content
        return window
    }
}
