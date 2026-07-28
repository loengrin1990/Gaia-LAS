"""Hard wall-clock boundary for one context-compiler model call.

The context compiler is the only consumer of this module.  A separate spawned
process means a stuck HTTP client cannot retain the job worker or its lock.
"""
from __future__ import annotations

import multiprocessing
import time
import json
import urllib.request
from typing import Any, Callable


POLL_INTERVAL_SECONDS = 0.05
TERMINATE_GRACE_SECONDS = 0.35


class ContextModelExecutorError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _safe_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the IPC payload to fields needed by backend validation only."""
    allowed = {
        "ok", "status", "answer", "provider", "model", "route",
        "prompt_chars_sent", "prompt_compacted", "done", "done_reason",
        "prompt_eval_count", "eval_count", "total_duration", "load_duration",
        "prompt_eval_duration", "eval_duration", "num_ctx", "num_predict",
    }
    return {key: result.get(key) for key in allowed if key in result}


def _context_model_child(sender: Any, payload: dict[str, Any]) -> None:
    """Spawn-safe target.  It never writes prompt or model output to logs."""
    try:
        if payload.get("operation") == "preload":
            request = urllib.request.Request(payload["endpoint"], data=json.dumps({"model": payload["model"], "messages": [], "stream": False, "keep_alive": payload.get("keep_alive", "30m")}).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=payload["timeout"]) as response:
                data = json.loads(response.read().decode("utf-8"))
            sender.send({"kind": "result", "result": {"ok": bool(data.get("done", True)), "done_reason": str(data.get("done_reason") or ""), "load_duration": data.get("load_duration"), "total_duration": data.get("total_duration")}})
            return
        if payload.get("operation") == "unload":
            request = urllib.request.Request(payload["endpoint"].replace("/api/chat", "/api/generate"), data=json.dumps({"model": payload["model"], "keep_alive": 0}).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=payload["timeout"]) as response:
                response.read()
            sender.send({"kind": "result", "result": {"ok": True}})
            return
        from .local_llm import run_local_llm_prompt

        result = run_local_llm_prompt(
            payload["prompt"], payload["system"], timeout=payload["timeout"],
            temperature=payload["temperature"], task=payload["task"],
            response_schema=payload.get("response_schema"),
        )
        sender.send({"kind": "result", "result": _safe_result(result)})
    except Exception:
        try:
            sender.send({"kind": "error"})
        except Exception:
            pass
    finally:
        sender.close()


def _stop_process(process: Any) -> None:
    """Terminate, escalate to kill, and always reap the child."""
    if process.is_alive():
        process.terminate()
        process.join(TERMINATE_GRACE_SECONDS)
    if process.is_alive():
        process.kill()
    process.join(TERMINATE_GRACE_SECONDS)


def execute_context_model_call(
    payload: dict[str, Any],
    timeout_seconds: float,
    cancel_event: Any = None,
    *,
    worker: Callable[[Any, dict[str, Any]], None] = _context_model_child,
) -> dict[str, Any]:
    """Run one call in a spawned child and enforce a real wall-clock deadline."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=(sender, payload), daemon=False)
    deadline = time.monotonic() + max(0.01, float(timeout_seconds))
    try:
        process.start()
        sender.close()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise ContextModelExecutorError("cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ContextModelExecutorError("timeout")
            if receiver.poll(min(POLL_INTERVAL_SECONDS, remaining)):
                message = receiver.recv()
                if not isinstance(message, dict) or message.get("kind") != "result" or not isinstance(message.get("result"), dict):
                    raise ContextModelExecutorError("result")
                process.join(TERMINATE_GRACE_SECONDS)
                if process.is_alive():
                    raise ContextModelExecutorError("process")
                return message["result"]
            if not process.is_alive():
                raise ContextModelExecutorError("process")
    except (EOFError, OSError):
        raise ContextModelExecutorError("process") from None
    finally:
        receiver.close()
        sender.close()
        _stop_process(process)
