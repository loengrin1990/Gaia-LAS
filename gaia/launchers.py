from __future__ import annotations

import subprocess
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
    if module == "lm":
        return launch_path(SETTINGS.lm_studio_launcher)
    if module == "transcriber":
        return launch_path(SETTINGS.transcriber_launcher)
    return {"ok": False, "error": "Неизвестный модуль"}
