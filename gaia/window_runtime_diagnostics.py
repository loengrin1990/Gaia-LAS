"""Narrow, opt-in diagnostics for the macOS window process.

This writer intentionally does not reuse the review diagnostics writer: it has a
different lifecycle and must prove the Python -> JXA handoff independently.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Mapping


DIAGNOSTICS_FLAG = "GAIA_STAGE6_RUNTIME_DIAGNOSTICS"
DIAGNOSTICS_PATH = "GAIA_STAGE6_DIAGNOSTICS_PATH"
DIAGNOSTICS_CONFIGURATION_ID = "GAIA_STAGE6_DIAGNOSTICS_CONFIGURATION_ID"

_ALLOWED_FIELDS = {
    "diagnostics_flag_present",
    "diagnostics_path_present",
    "diagnostics_configuration_matches",
    "controller_matches_active_configuration",
    "configuration_matches_active_webview",
    "handler_name_matches",
    "delegate_retained",
    "page_bridge_available",
    "page_message_received",
    "completion_called",
    "callback_received",
    "panel_started",
    "upload_flow_started",
    "selected_url_count",
    "error_code",
}


def diagnostics_enabled(environment: Mapping[str, str] | None = None) -> bool:
    environment = os.environ if environment is None else environment
    return environment.get(DIAGNOSTICS_FLAG) == "1"


def configuration_id(environment: Mapping[str, str] | None = None) -> str:
    environment = os.environ if environment is None else environment
    path = environment.get(DIAGNOSTICS_PATH, "")
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16] if path else ""


def window_diagnostics_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the exact opt-in environment explicitly handed to osascript."""
    source = os.environ if environment is None else environment
    child_environment = dict(source)
    if diagnostics_enabled(source):
        child_environment[DIAGNOSTICS_CONFIGURATION_ID] = configuration_id(source)
    else:
        child_environment.pop(DIAGNOSTICS_CONFIGURATION_ID, None)
    return child_environment


def write_event(event_code: str, fields: Mapping[str, object] | None = None, environment: Mapping[str, str] | None = None) -> dict[str, object]:
    """Append one safe JSONL event and synchronise it before returning."""
    environment = os.environ if environment is None else environment
    if not diagnostics_enabled(environment):
        return {"enabled": False, "written": False}

    path_value = environment.get(DIAGNOSTICS_PATH, "")
    if not path_value:
        return {"enabled": True, "written": False, "error_code": "diagnostics_path_missing"}
    path = Path(path_value)
    if not path.parent.exists() or not path.parent.is_dir():
        return {"enabled": True, "written": False, "error_code": "diagnostics_parent_missing"}

    event = {
        "component": "window_runtime_diagnostics",
        "event_code": event_code,
        "configuration_id": configuration_id(environment),
    }
    for key, value in (fields or {}).items():
        if key in _ALLOWED_FIELDS and isinstance(value, (bool, int, str)):
            event[key] = value
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        return {"enabled": True, "written": False, "error_code": "diagnostics_write_failed"}
    return {"enabled": True, "written": True}
