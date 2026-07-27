import AppKit
import WebKit

final class GaiaHostController: NSObject, WKUIDelegate, WKNavigationDelegate {
    private enum ProbeResult: Equatable { case gaia, otherService, unavailable }
    private let diagnostics = NativeDiagnostics()
    private let correlationID = UUID().uuidString
    private var ownedBackend: Process?
    private var origin: GaiaOrigin?
    private var window: NSWindow?
    private var webView: WKWebView?

    func start() {
        diagnostics.emit("native_host_started", fields: ["correlation_id": correlationID])
        let locator = BackendLocator()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.connect(locator: locator)
        }
    }

    func close() {
        guard let process = ownedBackend, process.isRunning else {
            diagnostics.emit("native_host_closed", fields: ["correlation_id": correlationID, "owned": false])
            return
        }
        diagnostics.emit("owned_backend_termination_started", fields: ["correlation_id": correlationID, "owned": true])
        process.terminate()
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let deadline = Date().addingTimeInterval(3)
            while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
            self?.diagnostics.emit("owned_backend_terminated", fields: ["correlation_id": self?.correlationID ?? "", "owned": true])
        }
        diagnostics.emit("native_host_closed", fields: ["correlation_id": correlationID, "owned": true])
    }

    private func connect(locator: BackendLocator) {
        do {
            let repository = try locator.repositoryRoot()
            let selectedOrigin = try locator.configuredOrigin(in: repository)
            origin = selectedOrigin
            diagnostics.emit("backend_probe_started", fields: ["correlation_id": correlationID, "port": selectedOrigin.port])
            switch probe(origin: selectedOrigin) {
            case .gaia:
                diagnostics.emit("backend_attached", fields: ["correlation_id": correlationID, "owned": false, "port": selectedOrigin.port])
                loadWebView(origin: selectedOrigin)
                return
            case .otherService:
                throw GaiaHostError.portOccupied
            case .unavailable:
                break
            }
            try launchBackend(locator: locator, repository: repository, origin: selectedOrigin)
            guard waitForGaia(origin: selectedOrigin, timeout: 8) else { throw GaiaHostError.backendUnavailable }
            diagnostics.emit("backend_ready", fields: ["correlation_id": correlationID, "owned": true, "port": selectedOrigin.port])
            loadWebView(origin: selectedOrigin)
        } catch let error as GaiaHostError {
            diagnostics.emit("backend_failed", fields: ["correlation_id": correlationID, "error_code": code(for: error)])
            showError(error)
        } catch {
            diagnostics.emit("backend_failed", fields: ["correlation_id": correlationID, "error_code": "unexpected"])
            showMessage(title: "Не удалось запустить локальный сервер Gaia", detail: "Проверьте локальную конфигурацию Gaia и повторите запуск.")
        }
    }

    private func launchBackend(locator: BackendLocator, repository: URL, origin: GaiaOrigin) throws {
        let python = try locator.pythonExecutable(in: repository)
        let process = Process()
        process.executableURL = python
        process.arguments = [repository.appendingPathComponent("app.py").path, "--no-window"]
        process.currentDirectoryURL = repository
        process.terminationHandler = { [weak self] _ in
            guard let self, self.ownedBackend === process else { return }
            self.diagnostics.emit("backend_failed", fields: ["correlation_id": self.correlationID, "owned": true, "port": origin.port, "error_code": "terminated"])
            self.showMessage(title: "Локальный сервер Gaia неожиданно остановился", detail: "Закройте окно и запустите Gaia снова.")
        }
        diagnostics.emit("backend_launch_started", fields: ["correlation_id": correlationID, "owned": true, "port": origin.port])
        try process.run()
        ownedBackend = process
    }

    private func probe(origin: GaiaOrigin) -> ProbeResult {
        let semaphore = DispatchSemaphore(value: 0)
        var result: ProbeResult = .unavailable
        var status = 0
        let request = URLRequest(url: origin.url.appendingPathComponent("api/runtime"), timeoutInterval: 0.7)
        URLSession.shared.dataTask(with: request) { data, response, _ in
            status = (response as? HTTPURLResponse)?.statusCode ?? 0
            if let data, let runtime = try? JSONDecoder().decode(BackendRuntime.self, from: data), runtime.isGaia {
                result = .gaia
            } else if status > 0 {
                result = .otherService
            }
            semaphore.signal()
        }.resume()
        _ = semaphore.wait(timeout: .now() + 1)
        diagnostics.emit("backend_probe_started", fields: ["correlation_id": correlationID, "port": origin.port, "http_status": status])
        return result
    }

    private func waitForGaia(origin: GaiaOrigin, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if probe(origin: origin) == .gaia { return true }
            Thread.sleep(forTimeInterval: 0.15)
        }
        return false
    }

    private func loadWebView(origin: GaiaOrigin) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            let view = WKWebView(frame: .zero)
            view.uiDelegate = self
            view.navigationDelegate = self
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 850), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            window.title = "Gaia"
            window.contentView = view
            window.makeKeyAndOrderFront(nil)
            self.window = window
            self.webView = view
            self.diagnostics.emit("webview_load_started", fields: ["correlation_id": self.correlationID, "port": origin.port])
            view.load(URLRequest(url: origin.url))
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let origin, let url = navigationAction.request.url, NavigationPolicy(origin: origin).allows(url) else {
            diagnostics.emit("navigation_blocked", fields: ["correlation_id": correlationID])
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        diagnostics.emit("webview_load_finished", fields: ["correlation_id": correlationID, "port": origin?.port ?? 0])
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        diagnostics.emit("open_panel_requested", fields: ["correlation_id": correlationID])
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = true
        var callCount = 0
        func finish(_ urls: [URL]?, result: String) {
            guard callCount == 0 else { return }
            callCount += 1
            completionHandler(urls)
            diagnostics.emit("completion_handler_called", fields: ["correlation_id": correlationID, "result": result, "selected_url_count": urls?.count ?? 0, "completion_call_count": callCount])
        }
        let parent = window ?? webView.window
        if let parent {
            panel.beginSheetModal(for: parent) { response in
                response == .OK ? finish(panel.urls, result: "accepted") : finish(nil, result: "cancelled")
            }
        } else {
            let response = panel.runModal()
            response == .OK ? finish(panel.urls, result: "accepted") : finish(nil, result: "cancelled")
        }
    }

    private func showError(_ error: GaiaHostError) {
        switch error {
        case .pythonNotFound: showMessage(title: "Не найдено окружение Python для Gaia", detail: "Укажите Python в локальной настройке и повторите запуск.")
        case .portOccupied: showMessage(title: "Порт занят другой программой", detail: "Gaia не подключилась к неизвестному локальному сервису.")
        default: showMessage(title: "Не удалось запустить локальный сервер Gaia", detail: "Проверьте локальную конфигурацию Gaia и повторите запуск.")
        }
    }

    private func showMessage(title: String, detail: String) {
        DispatchQueue.main.async {
            let alert = NSAlert()
            alert.messageText = title
            alert.informativeText = detail
            alert.runModal()
            NSApplication.shared.terminate(nil)
        }
    }

    private func code(for error: GaiaHostError) -> String {
        switch error { case .pythonNotFound: return "python_not_found"; case .repositoryNotFound: return "repository_not_found"; case .portOccupied: return "port_occupied"; case .backendUnavailable: return "backend_unavailable"; case .invalidConfiguration: return "invalid_configuration" }
    }
}
