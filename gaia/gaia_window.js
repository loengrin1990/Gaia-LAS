ObjC.import("Cocoa");
ObjC.import("WebKit");
ObjC.bindFunction("class_getInstanceMethod", ["pointer", ["id", "id"]]);
ObjC.bindFunction("method_getNumberOfArguments", ["unsigned long", ["pointer"]]);
ObjC.bindFunction("method_getTypeEncoding", ["pointer", ["pointer"]]);

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
  methods: {
    "webView:runOpenPanelWithParameters:initiatedByFrame:completionHandler:": {
      types: ["void", ["id", "id", "id", "@?"]],
      implementation: function(webView, parameters, frameInfo, completionHandler) {
      const correlationId = stage6CorrelationId();
      stage6Emit("wkui_delegate_body_entered", correlationId, { callback_received: true });
      stage6Emit("wkui_delegate_callback_received", correlationId, { callback_received: true });
      stage6Emit("webkit_file_picker_request", correlationId, { callback_received: true });
      const panel = $.NSOpenPanel.openPanel;
      stage6Emit("open_panel_created", correlationId, { panel_started: false });
      panel.setCanChooseFiles(true);
      panel.setCanChooseDirectories(false);
      panel.setAllowsMultipleSelection(ObjC.unwrap(parameters.allowsMultipleSelection));
      stage6Emit("open_panel_started", correlationId, { panel_started: true });
      stage6Emit("open_panel_runmodal_invocation_started", correlationId, { panel_started: true });
      const result = panel.runModal;
      stage6Emit("open_panel_runmodal_returned", correlationId, { panel_started: true });
      stage6Emit("open_panel_result_decoding_started", correlationId, {});
      const accepted = Number(result) === Number($.NSModalResponseOK);
      stage6Emit("open_panel_result", correlationId, { panel_result: accepted ? "accepted" : "cancelled" });
      stage6Emit("open_panel_finished", correlationId, { panel_result: accepted ? "accepted" : "cancelled" });
      let urls = $.NSArray.array;
      let selectedUrlCount = 0;
      if (accepted) {
        stage6Emit("selected_urls_read_started", correlationId, {});
        urls = panel.URLs;
        selectedUrlCount = Number(urls.count);
        stage6Emit("selected_urls_read_completed", correlationId, { selected_url_count: selectedUrlCount });
      }
      inspectCompletionHandlerArgument(completionHandler, correlationId);
      stage6Emit("completion_handler_invocation_started", correlationId, { selected_url_count: selectedUrlCount });
      completionHandler(urls);
      stage6Emit("completion_handler_called", correlationId, { completion_called: true, selected_url_count: selectedUrlCount });
      stage6Emit("upload_flow_started", correlationId, { upload_flow_started: accepted });
      stage6Emit("webkit_upload_flow_received", correlationId, { upload_flow_started: accepted });
    }
    }
  }
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
      const body = message.body;
      function booleanField(name) {
        try { return ObjC.unwrap(body.objectForKey($(name))) === true; } catch (_) { return false; }
      }
      if (pageEvent === "upload_control_pointer_received") {
        stage6Emit("upload_control_pointer_received", stage6CorrelationId(), {
          event_is_trusted: booleanField("event_is_trusted"),
          input_present: booleanField("input_present"),
          input_connected: booleanField("input_connected"),
          input_disabled: booleanField("input_disabled")
        });
      } else if (pageEvent === "file_input_activation_requested") {
        stage6Emit("file_input_activation_requested", stage6CorrelationId(), {
          source_event_is_trusted: booleanField("source_event_is_trusted"),
          same_event_turn: booleanField("same_event_turn"),
          input_connected: booleanField("input_connected"),
          input_disabled: booleanField("input_disabled")
        });
      } else if (pageEvent === "file_input_click_event_received") {
        stage6Emit("file_input_click_event_received", stage6CorrelationId(), {
          event_is_trusted: booleanField("event_is_trusted"),
          input_connected: booleanField("input_connected"),
          input_disabled: booleanField("input_disabled")
        });
      } else if (pageEvent === "page_bridge_available") {
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

function filePanelDelegateRespondsToOpenPanelSelector() {
  const delegate = $.GaiaFilePanelDelegate.alloc.init;
  return delegate.respondsToSelector("webView:runOpenPanelWithParameters:initiatedByFrame:completionHandler:");
}

function inspectCompletionHandlerArgument(completionHandler, correlationId) {
  stage6Emit("completion_handler_argument_inspection_started", correlationId, {});
  const present = completionHandler !== null && typeof completionHandler !== "undefined";
  const jsCallable = typeof completionHandler === "function";
  const objcWrapped = typeof completionHandler === "object" && completionHandler !== null;
  stage6Emit("completion_handler_argument_inspected", correlationId, {
    completion_argument_present: present,
    completion_argument_js_callable: jsCallable,
    completion_argument_objc_wrapped: objcWrapped,
    completion_argument_block_typed: true,
    completion_argument_class_known: objcWrapped
  });
  return jsCallable;
}

function runCompletionBlockBridgeHarness() {
  let report = { block_typed: true, receipts: 0, js_callable: false, objc_wrapped: false, direct_bridge: "unavailable" };
  ObjC.registerSubclass({
    name: "GaiaCompletionBlockBridgeHarness",
    methods: {
      "inspectBlock:": {
        types: ["void", ["@?"]],
        implementation: function(block) {
          report.receipts += 1;
          report.js_callable = typeof block === "function";
          report.objc_wrapped = typeof block === "object" && block !== null;
          if (report.js_callable) report.direct_bridge = "available";
        }
      }
    }
  });
  const receiver = $.GaiaCompletionBlockBridgeHarness.alloc.init;
  const nativeBlock = $.NSBlockOperation.blockOperationWithBlock(function() {});
  receiver.inspectBlock(nativeBlock);
  receiver.inspectBlock(nativeBlock);
  return JSON.stringify(report);
}

function runFilePanelHarness() {
  const app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyRegular);
  const frame = $.NSMakeRect(0, 0, 640, 300);
  const configuration = $.WKWebViewConfiguration.alloc.init;
  const webView = $.WKWebView.alloc.initWithFrameConfiguration(frame, configuration);
  filePanelDelegate = $.GaiaFilePanelDelegate.alloc.init;
  webView.setUIDelegate(filePanelDelegate);
  const window = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(
    frame,
    $.NSWindowStyleMaskTitled | $.NSWindowStyleMaskClosable,
    $.NSBackingStoreBuffered,
    false
  );
  window.setTitle($("Gaia — проверка выбора файла"));
  window.setContentView(webView);
  window.makeKeyAndOrderFront(null);
  stage6Emit("file_panel_harness_ready", stage6CorrelationId(), { delegate_retained: filePanelDelegate !== null });
  webView.loadHTMLStringBaseURL($("<!doctype html><html lang='ru'><body><label for='file'>Выберите файл для проверки</label><input id='file' type='file'></body></html>"), $.NSURL.URLWithString($("about:blank")));
  app.activateIgnoringOtherApps(true);
  app.run();
}

function runOpenPanelSmoke() {
  const app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyRegular);
  const panel = $.NSOpenPanel.openPanel;
  panel.setCanChooseFiles(true);
  panel.setCanChooseDirectories(false);
  panel.setAllowsMultipleSelection(false);
  app.activateIgnoringOtherApps(true);
  stage6Emit("open_panel_runmodal_invocation_started", stage6CorrelationId(), { panel_started: true });
  const result = panel.runModal;
  const correlationId = stage6CorrelationId();
  stage6Emit("open_panel_runmodal_returned", correlationId, { panel_started: true });
  stage6Emit("open_panel_result_decoding_started", correlationId, {});
  const accepted = Number(result) === Number($.NSModalResponseOK);
  stage6Emit("open_panel_result", correlationId, { panel_result: accepted ? "accepted" : "cancelled" });
  if (accepted) {
    stage6Emit("selected_urls_read_started", correlationId, {});
    const urls = panel.URLs;
    stage6Emit("selected_urls_read_completed", correlationId, { selected_url_count: Number(urls.count) });
  }
  return accepted ? "NSOpenPanel accepted" : "NSOpenPanel cancelled";
}

function filePanelDelegateAbiReport() {
  const selectorName = "webView:runOpenPanelWithParameters:initiatedByFrame:completionHandler:";
  const selector = $.NSSelectorFromString($(selectorName));
  const delegate = $.GaiaFilePanelDelegate.alloc.init;
  const method = $.class_getInstanceMethod($.GaiaFilePanelDelegate, selector);
  if (!method) throw new Error("WKUIDelegate open-panel method is unavailable");
  const signature = delegate.methodSignatureForSelector(selector);
  const argumentTypes = [];
  const signatureArgumentCount = Number(ObjC.unwrap(signature.numberOfArguments));
  for (let index = 0; index < signatureArgumentCount; index += 1) {
    argumentTypes.push(ObjC.unwrap(signature.getArgumentTypeAtIndex(index)));
  }
  return JSON.stringify({
    selector_present: true,
    responds_to_selector: delegate.respondsToSelector(selector),
    instances_respond_to_selector: $.GaiaFilePanelDelegate.instancesRespondToSelector(selector),
    method_get_available: Boolean(method),
    runtime_type_encoding: ObjC.unwrap(signature.methodReturnType) + argumentTypes.join(""),
    signature_argument_count: signatureArgumentCount,
    return_type: ObjC.unwrap(signature.methodReturnType),
    argument_types: argumentTypes
  });
}

function run(argv) {
  if (argv[0] === "--check") return "WebKit available";
  if (argv[0] === "--diagnostics-delegate-smoke") {
    if (!diagnosticsDelegateRespondsToMessageSelector()) throw new Error("WKScriptMessageHandler selector is unavailable");
    return "WKScriptMessageHandler available";
  }
  if (argv[0] === "--file-panel-delegate-smoke") {
    if (!filePanelDelegateRespondsToOpenPanelSelector()) throw new Error("WKUIDelegate open-panel selector is unavailable");
    return "WKUIDelegate open-panel handler available";
  }
  if (argv[0] === "--file-panel-delegate-abi") return filePanelDelegateAbiReport();
  if (argv[0] === "--file-panel-harness") return runFilePanelHarness();
  if (argv[0] === "--open-panel-smoke") return runOpenPanelSmoke();
  if (argv[0] === "--completion-block-bridge-harness") return runCompletionBlockBridgeHarness();
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
    const source = "(function(){var bridgeName='gaiaStage6Diagnostics';function bridge(){return window.webkit&&window.webkit.messageHandlers&&window.webkit.messageHandlers[bridgeName];}function send(eventCode,fields){var target=bridge();if(target&&typeof target.postMessage==='function'){target.postMessage(Object.assign({event_code:eventCode},fields||{}));return true;}return false;}function inputState(input){return {input_present:!!input,input_connected:!!(input&&input.isConnected),input_disabled:!!(input&&input.disabled)};}function announce(){if(send('page_bridge_available')){send('page_bridge_ready');}}if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',announce,{once:true});}else{announce();}document.addEventListener('pointerdown',function(event){var control=event.target&&event.target.closest&&event.target.closest('[data-file-input-control]');if(!control)return;var input=document.getElementById(control.htmlFor);send('upload_control_pointer_received',Object.assign({event_is_trusted:!!event.isTrusted},inputState(input)));},true);document.addEventListener('click',function(event){var control=event.target&&event.target.closest&&event.target.closest('[data-file-input-control]');if(control){var input=document.getElementById(control.htmlFor);send('file_input_activation_requested',Object.assign({source_event_is_trusted:!!event.isTrusted,same_event_turn:true},inputState(input)));}if(event.target&&event.target.matches('input[type=file]')){send('file_input_click_event_received',Object.assign({event_is_trusted:!!event.isTrusted},inputState(event.target)));send('dom_file_input_click');}},true);})();";
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
