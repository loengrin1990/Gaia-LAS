from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from .config import SETTINGS
from .window_runtime_diagnostics import diagnostics_enabled, window_diagnostics_environment, write_event


WINDOW_PROCESS: subprocess.Popen[bytes] | None = None
RUNTIME_READY_TIMEOUT_SECONDS = 8.0


def native_host_app_path() -> Path:
    return Path(__file__).parents[1] / "native" / "macos" / "build" / "DerivedData" / "Build" / "Products" / "Debug" / "Gaia.app"


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
    url = f"http://{SETTINGS.host}:{SETTINGS.port}"
    ready = wait_for_runtime(url, runtime_id)
    if not ready.get("ok"):
        return {"ok": False, "error": str(ready["error"])}
    actual_runtime_id = str(ready["runtime"]["runtime_id"])
    if WINDOW_PROCESS and WINDOW_PROCESS.poll() is None:
        return {"ok": True, "message": "Окно Gaia уже открыто для текущего запуска."}
    try:
        native_host = native_host_app_path()
        if native_host.exists():
            WINDOW_PROCESS = subprocess.Popen(["open", str(native_host)], start_new_session=True)
            return {"ok": True, "message": "Gaia открыта в нативном системном окне."}
        script = Path(__file__).with_name("gaia_window.js")
        if not script.exists():
            return {"ok": False, "error": "Не найден системный launcher Gaia."}
        environment = window_diagnostics_environment()
        diagnostic_result = write_event(
            "diagnostics_python_enabled",
            {
                "diagnostics_flag_present": diagnostics_enabled(),
                "diagnostics_path_present": bool(os.environ.get("GAIA_STAGE6_DIAGNOSTICS_PATH")),
            },
        )
        if diagnostic_result.get("enabled"):
            print(f"gaia_stage6_diagnostics:{diagnostic_result.get('error_code') or 'diagnostics_python_enabled'}", file=sys.stderr)
        WINDOW_PROCESS = subprocess.Popen(
            ["/usr/bin/osascript", "-l", "JavaScript", str(script), f"{url}/?runtime={actual_runtime_id}"],
            start_new_session=True,
            env=environment,
        )
        return {"ok": True, "message": "Gaia открыта в отдельном системном окне."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def close_gaia_window() -> None:
    global WINDOW_PROCESS
    if WINDOW_PROCESS and WINDOW_PROCESS.poll() is None:
        WINDOW_PROCESS.terminate()
    WINDOW_PROCESS = None
