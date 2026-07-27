ObjC.import("Cocoa");
ObjC.import("WebKit");

let filePanelDelegate = null;
let runtimeDiagnosticsDelegate = null;

function stage6DiagnosticsEnabled() {
  return ObjC.unwrap($.NSProcessInfo.processInfo.environment.objectForKey($("GAIA_STAGE6_RUNTIME_DIAGNOSTICS"))) === "1";
}

function stage6DiagnosticPath() {
  return ObjC.unwrap($.NSProcessInfo.processInfo.environment.objectForKey($("GAIA_STAGE6_DIAGNOSTICS_PATH"))) || "";
}

function stage6CorrelationId() {
  return `stage6-${$.NSUUID.UUID.UUIDString.js.replace(/-/g, "").toLowerCase()}`;
}

function stage6Emit(eventCode, correlationId, fields) {
  if (!stage6DiagnosticsEnabled()) return;
  const path = stage6DiagnosticPath();
  if (!path) return;
  const event = Object.assign({ timestamp: new Date().toISOString(), component: "file_picker", event_code: eventCode, correlation_id: correlationId }, fields || {});
  try {
    const handle = $.NSFileHandle.fileHandleForWritingAtPath($(path));
    if (!handle) return;
    handle.seekToEndOfFile();
    handle.writeData($(JSON.stringify(event) + "\n").dataUsingEncoding($.NSUTF8StringEncoding));
    handle.closeFile();
  } catch (_) {}
}

ObjC.registerSubclass({
  name: "GaiaFilePanelDelegate",
  methods: [{
    selector: "webView:runOpenPanelWithParameters:initiatedByFrame:completionHandler:",
    types: ["void", ["id", "id", "id", "id", "id"]],
    implementation: function(webView, parameters, frameInfo, completionHandler) {
      const correlationId = stage6CorrelationId();
      stage6Emit("webkit_file_picker_request", correlationId, { callback_received: true });
      const panel = $.NSOpenPanel.openPanel;
      stage6Emit("open_panel_created", correlationId, { panel_started: false });
      panel.setCanChooseFiles(true);
      panel.setCanChooseDirectories(false);
      panel.setAllowsMultipleSelection(ObjC.unwrap(parameters.allowsMultipleSelection));
      stage6Emit("open_panel_started", correlationId, { panel_started: true });
      const result = panel.runModal();
      const accepted = result === $.NSModalResponseOK;
      const urls = accepted ? panel.URLs : null;
      stage6Emit("open_panel_finished", correlationId, { panel_result: accepted ? "accepted" : "cancelled" });
      completionHandler(urls);
      stage6Emit("completion_handler_called", correlationId, { completion_called: true, selected_url_count: accepted ? Number(panel.URLs.count) : 0 });
      stage6Emit("webkit_upload_flow_received", correlationId, { upload_flow_started: accepted });
    }
  }]
});

ObjC.registerSubclass({
  name: "GaiaRuntimeDiagnosticsDelegate",
  methods: [{
    selector: "userContentController:didReceiveScriptMessage:",
    types: ["void", ["id", "id", "id"]],
    implementation: function(controller, message) {
      const eventCode = ObjC.unwrap(message.name) === "gaiaStage6Diagnostics" ? "dom_file_input_click" : "unexpected_script_message";
      stage6Emit(eventCode, stage6CorrelationId(), { dom_click_registered: eventCode === "dom_file_input_click" });
    }
  }]
});

function run(argv) {
  if (argv[0] === "--check") return "WebKit available";

  const address = argv[0] || "http://127.0.0.1:8787";
  const url = $.NSURL.URLWithString($(address));
  if (!url) throw new Error(`Invalid Gaia URL: ${address}`);

  const app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyRegular);
  const frame = $.NSMakeRect(0, 0, 1360, 900);
  const configuration = $.WKWebViewConfiguration.alloc.init;
  if (stage6DiagnosticsEnabled()) {
    runtimeDiagnosticsDelegate = $.GaiaRuntimeDiagnosticsDelegate.alloc.init;
    const source = "document.addEventListener('click', function(event) { if (event.target && event.target.matches('input[type=file]')) { window.webkit.messageHandlers.gaiaStage6Diagnostics.postMessage({}); } }, true);";
    const script = $.WKUserScript.alloc.initWithSourceInjectionTimeForMainFrameOnly($(source), $.WKUserScriptInjectionTimeAtDocumentStart, true);
    configuration.userContentController.addUserScript(script);
    configuration.userContentController.addScriptMessageHandlerName(runtimeDiagnosticsDelegate, $("gaiaStage6Diagnostics"));
  }
  const webView = $.WKWebView.alloc.initWithFrameConfiguration(frame, configuration);
  filePanelDelegate = $.GaiaFilePanelDelegate.alloc.init;
  webView.setUIDelegate(filePanelDelegate);
  const style = $.NSWindowStyleMaskTitled
    | $.NSWindowStyleMaskClosable
    | $.NSWindowStyleMaskMiniaturizable
    | $.NSWindowStyleMaskResizable;
  const window = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(
    frame,
    style,
    $.NSBackingStoreBuffered,
    false
  );

  window.setTitle($("Gaia"));
  window.setTitlebarAppearsTransparent(true);
  window.setMinSize($.NSMakeSize(900, 640));
  window.setContentView(webView);
  window.makeKeyAndOrderFront(null);
  webView.loadRequest($.NSURLRequest.requestWithURL(url));
  app.activateIgnoringOtherApps(true);
  app.run();
}
