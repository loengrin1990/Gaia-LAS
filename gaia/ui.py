from pathlib import Path


def load_index_html() -> str:
    static = Path(__file__).with_name("static")
    html = (static / "index.html").read_text(encoding="utf-8")
    controller = (static / "context_compile_controller.js").read_text(encoding="utf-8")
    finder = (static / "material_finder.js").read_text(encoding="utf-8")
    html = html.replace("<!-- material-finder-controller -->", f"<script>{finder}</script>")
    preview = (static / "dialogue_context_preview.js").read_text(encoding="utf-8")
    html = html.replace("<!-- dialogue-context-preview -->", f"<script>{preview}</script>")
    evidence = (static / "operational_evidence.js").read_text(encoding="utf-8")
    html = html.replace("<!-- operational-evidence-viewer -->", f"<script>{evidence}</script>")
    return html.replace("<!-- context-compile-controller -->", f"<script>{controller}</script>")


INDEX_HTML = load_index_html()
