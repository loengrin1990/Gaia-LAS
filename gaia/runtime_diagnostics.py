"""Opt-in, content-free runtime diagnostics for Stage 6 investigations."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DIAGNOSTICS_FLAG = "GAIA_STAGE6_RUNTIME_DIAGNOSTICS"
DIAGNOSTICS_PATH = "GAIA_STAGE6_DIAGNOSTICS_PATH"


def diagnostics_enabled() -> bool:
    return os.environ.get(DIAGNOSTICS_FLAG) == "1"


def new_correlation_id() -> str:
    return f"stage6-{uuid.uuid4().hex}"


def emit(component: str, event_code: str, correlation_id: str, **fields: Any) -> None:
    """Append an allow-listed technical event only when explicitly enabled."""
    if not diagnostics_enabled():
        return
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "component": component,
        "event_code": event_code,
        "correlation_id": correlation_id,
        **{key: value for key, value in fields.items() if value is not None},
    }
    path_value = os.environ.get(DIAGNOSTICS_PATH, "").strip()
    if not path_value:
        return
    try:
        path = Path(path_value)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        # Observation must never alter the review or the system picker behaviour.
        pass
