import AppKit
import WebKit

@main
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var hostController: GaiaHostController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let controller = GaiaHostController()
        hostController = controller
        controller.start()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        hostController?.close()
        return .terminateNow
    }
}
