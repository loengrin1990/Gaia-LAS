"""Repeat the real core HTTP journey; optionally keep a disposable synthetic UI open.

No caller profile is loaded. Model providers are disabled before Gaia is imported.
Run from the feature checkout: python -B -m scripts.smoke_core_journey [--serve]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch


def main() -> int:
    parser = argparse.ArgumentParser(description="Основной Gaia: временная синтетическая проверка без моделей")
    parser.add_argument("--serve", action="store_true", help="После smoke оставить временный UI до Ctrl+C")
    args = parser.parse_args()
    if "gaia.config" in sys.modules:
        parser.error("Запустите команду в отдельном процессе, до импорта Gaia.")
    # Reserve an available loopback address without touching a running service.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="gaia-core-smoke-") as temporary:
        root = Path(temporary)
        paths = {key: str(root / key) for key in (
            "vault", "projects", "service_docs", "runs", "exports", "storage", "obsidian_work",
            "transcriber", "lm_studio_launcher", "transcriber_launcher",
        )}
        for key in ("vault", "projects", "service_docs", "runs", "exports", "storage", "obsidian_work"):
            Path(paths[key]).mkdir()
        config = {
            "config_version": 1, "paths": paths, "server": {"host": "127.0.0.1", "port": port},
            "endpoints": {"lm_studio": "http://127.0.0.1:9/v1/chat/completions"},
            "local_llm": {
                "default_provider": "disabled",
                "providers": {"disabled": {"type": "openai_compatible", "endpoint": "http://127.0.0.1:9/v1/chat/completions", "model": "disabled", "enabled": False}},
                "routes": {"context_compiler": {"provider": "disabled"}},
            },
        }
        path = root / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        os.environ["GAIA_CONFIG"] = str(path)
        from gaia.config import SETTINGS, ensure_dirs
        ensure_dirs()
        assert all(not item["enabled"] for item in SETTINGS.local_llm_providers.values())
        # Reuse the existing full intake/review/compiler/restart test, including Dialogue.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
        from test_e2e_validation import EndToEndValidationTests
        suite = unittest.TestSuite([EndToEndValidationTests("test_confirmed_material_reaches_context_summary_and_survives_restart")])
        with patch("gaia.server.Handler.log_message"):
            result = unittest.TextTestRunner(verbosity=1).run(suite)
        if not result.wasSuccessful():
            return 1
        print("Smoke OK: приём → проверка → принятый контекст → пакет/основание. Модель не вызывалась.", flush=True)
        if not args.serve:
            return 0

        from gaia.controlled_intake import ControlledIntake
        from gaia.context_compiler import ContextCompiler, ContextService
        from gaia.review import ReviewService
        import gaia.server as runtime
        from http.server import ThreadingHTTPServer
        project = "Учебная проба"
        folder = Path(paths["projects"]) / project
        folder.mkdir()
        (folder / "Память.md").write_text("# Учебная проба\nТолько искусственные данные.\n", encoding="utf-8")
        intake = ControlledIntake()
        # Reuse the two existing preparation fixtures; no new model scenario.
        for index, text in enumerate(("Использовать резервный маршрут.", "Резервный бюджет пока не согласован.")):
            admitted = intake.admit(project, [(f"synthetic-{index}.txt", text.encode())])
            workspace = admitted["workspace_id"]
            artifact = admitted["materials"][0]["sanitized_id"]
            review = ReviewService(intake.store, workspace, lambda _: {"status": "completed", "findings": []})
            review.start(artifact)
            review.confirm(artifact)
            def model(fragment):
                return {"candidates": [{"type": "risk" if index else "decision", "title": "Резервный бюджет" if index else "Резервный маршрут", "statement": fragment.strip(), "block": {"start": 0, "end": len(fragment)}, "confidence": "high", "requires_review": True}]}
            candidates = ContextCompiler(intake.store, workspace, model).compile(artifact)
            if index == 0:
                for item in candidates:
                    ContextService(intake.store, workspace).decide(item["id"], "confirm")

        runtime.INDEX_HTML = runtime.INDEX_HTML.replace("<body>", '<body><aside role="note" style="padding:16px;color:white;background:#543400">Учебная проба: только искусственные данные. Модели отключены. Один факт подтверждён тестом, второй ожидает решения. После остановки профиль удалится. Рабочая Gaia не подключена.</aside>')
        class QuietHandler(runtime.Handler):
            def log_message(self, *_):
                pass
        # Guard only this disposable process, without system/network changes.
        connect = socket.socket.connect
        def local_only(sock, address):
            if not isinstance(address, tuple) or address[:2] != ("127.0.0.1", port):
                raise ConnectionRefusedError("Synthetic UI: model/external connections disabled")
            return connect(sock, address)
        with ThreadingHTTPServer(("127.0.0.1", port), QuietHandler) as server, patch.object(socket.socket, "connect", local_only):
            print(f"Учебный основной UI: http://127.0.0.1:{port}/", flush=True)
            print("Только временный профиль. Не добавляйте реальные файлы. Остановить: Ctrl+C.", flush=True)
            try:
                server.serve_forever(poll_interval=0.25)
            except KeyboardInterrupt:
                pass
        print("Учебный сервер остановлен; временный профиль удаляется.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
