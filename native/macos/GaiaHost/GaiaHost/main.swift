import AppKit

let diagnostics = NativeDiagnostics()
let correlationID = UUID().uuidString
diagnostics.emit("native_entry_reached", fields: ["correlation_id": correlationID])

let application = NSApplication.shared
let appDelegate = AppDelegate(diagnostics: diagnostics, correlationID: correlationID)
diagnostics.emit("app_delegate_created", fields: ["correlation_id": correlationID])
application.delegate = appDelegate
diagnostics.emit("app_delegate_assigned", fields: ["correlation_id": correlationID])
application.setActivationPolicy(.regular)
application.activate(ignoringOtherApps: true)
application.run()
