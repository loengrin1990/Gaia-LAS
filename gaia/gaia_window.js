ObjC.import("Cocoa");
ObjC.import("WebKit");

ObjC.registerSubclass({
  name: "GaiaFilePanelDelegate",
  methods: [{
    selector: "webView:runOpenPanelWithParameters:initiatedByFrame:completionHandler:",
    types: ["void", ["id", "id", "id", "id", "id"]],
    implementation: function(webView, parameters, frameInfo, completionHandler) {
      const panel = $.NSOpenPanel.openPanel;
      panel.setCanChooseFiles(true);
      panel.setCanChooseDirectories(false);
      panel.setAllowsMultipleSelection(ObjC.unwrap(parameters.allowsMultipleSelection));
      panel.beginSheetModalForWindowCompletionHandler(webView.window, function(result) {
        completionHandler(result === $.NSModalResponseOK ? panel.URLs : null);
      });
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
  const webView = $.WKWebView.alloc.initWithFrameConfiguration(frame, configuration);
  const filePanelDelegate = $.GaiaFilePanelDelegate.alloc.init;
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
