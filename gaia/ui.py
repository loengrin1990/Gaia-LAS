from pathlib import Path


def load_index_html() -> str:
    static = Path(__file__).with_name("static")
    html = (static / "index.html").read_text(encoding="utf-8")
    session = (static / "session_recovery.js").read_text(encoding="utf-8")
    controller = (static / "context_compile_controller.js").read_text(encoding="utf-8")
    return html.replace("<!-- session-recovery -->", f"<script>{session}</script>").replace("<!-- context-compile-controller -->", f"<script>{controller}</script>")


INDEX_HTML = load_index_html()
