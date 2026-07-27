ObjC.import("Cocoa");
ObjC.import("WebKit");

let filePanelDelegate = null;
let runtimeDiagnosticsDelegate = null;
const stage6HandlerName = "gaiaStage6Diagnostics";

function stage6DiagnosticsEnabled() {
  return ObjC.unwrap($.NSProcessInfo.processInfo.environment.objectForKey($("GAIA_STAGE6_RUNTIME_DIAGNOSTICS"))) === "1";
}

function stage6DiagnosticPath() {
  return ObjC.unwrap($.NSProcessInfo.processInfo.environment.objectForKey($("GAIA_STAGE6_DIAGNOSTICS_PATH"))) || "";
}

function stage6DiagnosticConfigurationId() {
  return ObjC.unwrap($.NSProcessInfo.processInfo.environment.objectForKey($("GAIA_STAGE6_DIAGNOSTICS_CONFIGURATION_ID"))) || "";
}

function stage6DiagnosticStderr(eventCode) {
  if (!stage6DiagnosticsEnabled()) return;
  try {
    const data = $(`gaia_stage6_diagnostics:${eventCode}\n`).dataUsingEncoding($.NSUTF8StringEncoding);
    $.NSFileHandle.fileHandleWithStandardError.writeData(data);
  } catch (_) {
    // Diagnostic output must never affect the product window.
  }
}

function stage6CorrelationId() {
  return `stage6-${$.NSUUID.UUID.UUIDString.js.replace(/-/g, "").toLowerCase()}`;
}

function stage6Emit(eventCode, correlationId, fields) {
  if (!stage6DiagnosticsEnabled()) return false;
  stage6DiagnosticStderr(eventCode);
  const path = stage6DiagnosticPath();
  if (!path) {
    stage6DiagnosticStderr("diagnostics_path_missing");
    return false;
  }
  const parent = $(path).stringByDeletingLastPathComponent;
  if (!$.NSFileManager.defaultManager.fileExistsAtPath(parent)) {
    stage6DiagnosticStderr("diagnostics_parent_missing");
    return false;
  }
  const event = Object.assign({ timestamp: new Date().toISOString(), component: "file_picker", event_code: eventCode, correlation_id: correlationId, configuration_id: stage6DiagnosticConfigurationId() }, fields || {});
  try {
    if (!$.NSFileManager.defaultManager.fileExistsAtPath($(path))) {
      const created = $.NSFileManager.defaultManager.createFileAtPathContentsAttributes($(path), $.NSData.data, null);
      if (!created) {
        stage6DiagnosticStderr("diagnostics_path_unavailable");
        return false;
      }
    }
    const handle = $.NSFileHandle.fileHandleForWritingAtPath($(path));
    if (!handle) {
      stage6DiagnosticStderr("diagnostics_path_unavailable");
      return false;
    }
    handle.seekToEndOfFile;
    handle.writeData($(JSON.stringify(event) + "\n").dataUsingEncoding($.NSUTF8StringEncoding));
    handle.synchronizeFile;
    handle.closeFile;
    return true;
  } catch (_) {
    stage6DiagnosticStderr("diagnostics_write_failed");
    return false;
  }
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
  methods: {
    "userContentController:didReceiveScriptMessage:": {
    types: ["void", ["id", "id"]],
    implementation: function(controller, message) {
      if (ObjC.unwrap(message.name) !== stage6HandlerName) {
        stage6Emit("diagnostics_message_rejected", stage6CorrelationId(), { page_message_received: false });
        return;
      }
      let pageEvent = "";
      try {
        pageEvent = ObjC.unwrap(message.body.objectForKey($("event_code"))) || "";
      } catch (_) {}
      if (pageEvent === "page_bridge_available") {
        stage6Emit("diagnostics_page_bridge_available", stage6CorrelationId(), { page_bridge_available: true });
      } else if (pageEvent === "page_bridge_ready") {
        const correlationId = stage6CorrelationId();
        stage6Emit("diagnostics_page_message_received", correlationId, { page_message_received: true });
        stage6Emit("diagnostics_page_ready_recorded", correlationId, { page_message_received: true });
      } else if (pageEvent === "dom_file_input_click") {
        stage6Emit("dom_file_input_click", stage6CorrelationId(), { page_message_received: true });
      } else {
        stage6Emit("diagnostics_message_rejected", stage6CorrelationId(), { page_message_received: false });
      }
    }
    }
  }
});

function diagnosticsDelegateRespondsToMessageSelector() {
  const delegate = $.GaiaRuntimeDiagnosticsDelegate.alloc.init;
  return delegate.respondsToSelector("userContentController:didReceiveScriptMessage:");
}

function run(argv) {
  if (argv[0] === "--check") return "WebKit available";
  if (argv[0] === "--diagnostics-delegate-smoke") {
    if (!diagnosticsDelegateRespondsToMessageSelector()) throw new Error("WKScriptMessageHandler selector is unavailable");
    return "WKScriptMessageHandler available";
  }
  if (argv[0] === "--diagnostics-writer-smoke") {
    if (!stage6DiagnosticsEnabled()) throw new Error("Stage 6 diagnostics are disabled");
    const wrote = stage6Emit("diagnostics_window_process_enabled", stage6CorrelationId(), {
      diagnostics_flag_present: true,
      diagnostics_path_present: Boolean(stage6DiagnosticPath()),
      diagnostics_configuration_matches: Boolean(stage6DiagnosticConfigurationId())
    });
    if (!wrote) throw new Error("Stage 6 diagnostics writer is unavailable");
    return "Stage 6 diagnostics writer available";
  }

  const address = argv[0] || "http://127.0.0.1:8787";
  const url = $.NSURL.URLWithString($(address));
  if (!url) throw new Error(`Invalid Gaia URL: ${address}`);

  const app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyRegular);
  const frame = $.NSMakeRect(0, 0, 1360, 900);
  const configuration = $.WKWebViewConfiguration.alloc.init;
  if (stage6DiagnosticsEnabled()) {
    const correlationId = stage6CorrelationId();
    stage6Emit("diagnostics_window_process_enabled", correlationId, {
      diagnostics_flag_present: true,
      diagnostics_path_present: Boolean(stage6DiagnosticPath()),
      diagnostics_configuration_matches: Boolean(stage6DiagnosticConfigurationId())
    });
    runtimeDiagnosticsDelegate = $.GaiaRuntimeDiagnosticsDelegate.alloc.init;
    const source = "(function(){var bridgeName='gaiaStage6Diagnostics';function bridge(){return window.webkit&&window.webkit.messageHandlers&&window.webkit.messageHandlers[bridgeName];}function send(eventCode){var target=bridge();if(target&&typeof target.postMessage==='function'){target.postMessage({event_code:eventCode});return true;}return false;}function announce(){if(send('page_bridge_available')){send('page_bridge_ready');}}if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',announce,{once:true});}else{announce();}document.addEventListener('click',function(event){if(event.target&&event.target.matches('input[type=file]')){send('dom_file_input_click');}},true);})();";
    const script = $.WKUserScript.alloc.initWithSourceInjectionTimeForMainFrameOnly($(source), $.WKUserScriptInjectionTimeAtDocumentStart, true);
    configuration.userContentController.addUserScript(script);
    configuration.userContentController.addScriptMessageHandlerName(runtimeDiagnosticsDelegate, $(stage6HandlerName));
    stage6Emit("diagnostics_handler_registered", correlationId, {
      controller_matches_active_configuration: true,
      handler_name_matches: true,
      delegate_retained: runtimeDiagnosticsDelegate !== null
    });
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
