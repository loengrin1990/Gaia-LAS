"""Safe fingerprint for one running Gaia server process."""
from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path


API_CONTRACT_VERSION = 1
STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")
RUNTIME_ID = uuid.uuid4().hex


def _git_commit() -> str:
    root = Path(__file__).resolve().parents[1]
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def runtime_fingerprint() -> dict[str, object]:
    """Return only process and build metadata; never include workspace data."""
    frontend = Path(__file__).with_name("static") / "index.html"
    return {
        "ready": True,
        "runtime_id": RUNTIME_ID,
        "pid": os.getpid(),
        "started_at": STARTED_AT,
        "git_commit": _git_commit(),
        "api_contract_version": API_CONTRACT_VERSION,
        "frontend_hash": hashlib.sha256(frontend.read_bytes()).hexdigest(),
    }
