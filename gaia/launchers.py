from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from .config import SETTINGS


WINDOW_PROCESS: subprocess.Popen[bytes] | None = None
RUNTIME_READY_TIMEOUT_SECONDS = 8.0


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


def wait_for_runtime(url: str, expected_runtime_id: str = "", timeout_seconds: float = RUNTIME_READY_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Require Gaia's own safe runtime response, not just an open TCP port."""
    deadline = time.monotonic() + timeout_seconds
    last_error = "сервер Gaia не ответил"
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{url}/api/runtime", timeout=0.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("ready") is True and (not expected_runtime_id or payload.get("runtime_id") == expected_runtime_id):
                return {"ok": True, "runtime": payload}
            last_error = "ответ сервера Gaia не подтвердил ожидаемый запуск"
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    return {"ok": False, "error": f"Gaia не подтвердила готовность за {timeout_seconds:g} с: {last_error}."}


def launch_gaia_window(runtime_id: str = "") -> dict[str, Any]:
    global WINDOW_PROCESS
    script = Path(__file__).with_name("gaia_window.js")
    if not script.exists():
        return {"ok": False, "error": "Не найден системный launcher Gaia."}
    url = f"http://{SETTINGS.host}:{SETTINGS.port}"
    ready = wait_for_runtime(url, runtime_id)
    if not ready.get("ok"):
        return {"ok": False, "error": str(ready["error"])}
    actual_runtime_id = str(ready["runtime"]["runtime_id"])
    if WINDOW_PROCESS and WINDOW_PROCESS.poll() is None:
        return {"ok": True, "message": "Окно Gaia уже открыто для текущего запуска."}
    try:
        WINDOW_PROCESS = subprocess.Popen(["/usr/bin/osascript", "-l", "JavaScript", str(script), f"{url}/?runtime={actual_runtime_id}"], start_new_session=True)
        return {"ok": True, "message": "Gaia открыта в отдельном системном окне."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def close_gaia_window() -> None:
    global WINDOW_PROCESS
    if WINDOW_PROCESS and WINDOW_PROCESS.poll() is None:
        WINDOW_PROCESS.terminate()
    WINDOW_PROCESS = None
