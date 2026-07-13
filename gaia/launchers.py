from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .config import SETTINGS


def launch_path(path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "error": f"Файл не найден: {path}"}
    try:
        subprocess.Popen(["open", str(path)])
        return {"ok": True, "message": f"Запущено: {path.name}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def launch_module(module: str) -> dict[str, Any]:
    if module == "gaia":
        return launch_gaia_window()
    if module == "lm":
        return launch_path(SETTINGS.lm_studio_launcher)
    if module == "transcriber":
        return launch_path(SETTINGS.transcriber_launcher)
    return {"ok": False, "error": "Неизвестный модуль"}


def launch_gaia_window() -> dict[str, Any]:
    script = Path(__file__).with_name("gaia_window.js")
    if not script.exists():
        return {"ok": False, "error": "Не найден системный launcher Gaia."}
    url = f"http://{SETTINGS.host}:{SETTINGS.port}"
    try:
        subprocess.Popen(["/usr/bin/osascript", "-l", "JavaScript", str(script), url], start_new_session=True)
        return {"ok": True, "message": "Gaia открыта в отдельном системном окне."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
