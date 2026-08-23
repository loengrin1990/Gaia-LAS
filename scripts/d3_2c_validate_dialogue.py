from __future__ import annotations

import concurrent.futures
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gaia.controlled_intake import ControlledIntake


CONFIG = ROOT / "config.json"
HOST = "127.0.0.1"
PORT = 8787
BASE = f"http://{HOST}:{PORT}"
PROJECT = "SInt GateFlow"
BUNDLE = Path(
    "/Users/ilia/Documents/Gaia-pilot-evidence/"
    "2026-08-23_D3.2C-production-dialogue-v3.incomplete"
)

QUESTIONS = [
    ("Q1", "Какое сейчас окно действия пропуска?"),
    (
        "Q2",
        "Какая система авторитетна по активному и отозванному состоянию доступа?",
    ),
    (
        "Q3",
        "Нужно ли повторное согласование принимающего при продлении гостевого пропуска?",
    ),
    (
        "Q4",
        "Нужно ли новое согласование при продлении пропуска предсогласованного подрядчика?",
    ),
    ("Q5", "Можно ли продлить уже истёкший пропуск?"),
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(path: Path) -> str:
    if not path.exists():
        return "<missing>"

    h = hashlib.sha256()

    if path.is_file():
        h.update(path.name.encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        return h.hexdigest()

    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(str(item.relative_to(path)).encode())
        h.update(b"\0")
        try:
            h.update(item.read_bytes())
        except OSError as exc:
            h.update(f"<ERROR:{type(exc).__name__}>".encode())
        h.update(b"\0")

    return h.hexdigest()


def port_open() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.4):
            return True
    except OSError:
        return False


def find_key_values(obj, wanted: str):
    out = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == wanted:
                out.append(value)
            out.extend(find_key_values(value, wanted))
    elif isinstance(obj, list):
        for value in obj:
            out.extend(find_key_values(value, wanted))
    return out


def json_request(opener, method: str, path: str, payload=None, timeout=30):
    data = None
    headers = {"Accept": "application/json"}

    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    if method != "GET":
        headers["Origin"] = BASE

    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with opener.open(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw_body": body}
        return exc.code, parsed


def post_with_heartbeat(opener, path: str, payload: dict, label: str):
    started = time.monotonic()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            json_request,
            opener,
            "POST",
            path,
            payload,
            180,
        )

        while True:
            try:
                result = future.result(timeout=5)
                elapsed = time.monotonic() - started
                print(f"[DONE] {label} — elapsed {elapsed:.1f}s")
                return result, elapsed
            except concurrent.futures.TimeoutError:
                elapsed = time.monotonic() - started
                print(f"[RUNNING] {label} — elapsed {elapsed:.0f}s")
            except Exception as exc:
                elapsed = time.monotonic() - started
                print(
                    f"[FAILED] {label} — "
                    f"{type(exc).__name__}: {exc} — elapsed {elapsed:.1f}s"
                )
                return (
                    0,
                    {"exception": f"{type(exc).__name__}: {exc}"},
                ), elapsed


def assistant_text(conversation: dict) -> str:
    for message in reversed(conversation.get("messages") or []):
        if message.get("role") == "assistant":
            return str(message.get("masked_text") or message.get("text") or "")
    return ""


def append_response(
    responses: list[str],
    label: str,
    question: str,
    answer: str,
    elapsed: float,
    read_paths,
):
    responses.extend(
        [
            f"## {label}",
            "",
            f"**Question:** {question}",
            "",
            f"**Answer:** {answer}",
            "",
            f"- elapsed_seconds: {elapsed:.3f}",
            f"- read_path: {read_paths}",
            f"- san_leak: {'san_' in answer}",
            f"- src_leak: {'src_' in answer}",
            "",
        ]
    )


def create_conversation(opener, title: str):
    status, payload = json_request(
        opener,
        "POST",
        "/api/conversations",
        {"project": PROJECT, "title": title},
        timeout=10,
    )
    return status, payload, str(payload.get("id") or "")


def run_turn(opener, conversation_id: str, label: str, question: str):
    print()
    print("------------------------------------------------------------")
    print(f"[RUNNING] {label}")
    print(question)

    (status, payload), elapsed = post_with_heartbeat(
        opener,
        f"/api/conversations/{conversation_id}/messages",
        {"text": question, "run_local": True},
        label,
    )

    get_status, conversation = json_request(
        opener,
        "GET",
        f"/api/conversations/{conversation_id}",
        timeout=10,
    )

    answer = assistant_text(conversation) if get_status == 200 else ""
    read_paths = find_key_values(payload, "read_path")

    checks = {
        "http_201": status == 201,
        "conversation_readable": get_status == 200,
        "hybrid": "hybrid" in [str(x) for x in read_paths],
        "answer_present": bool(answer),
        "san_leak": "san_" in answer,
        "src_leak": "src_" in answer,
    }

    print()
    print(f"=== {label} VISIBLE ANSWER ===")
    print(answer or "<EMPTY>")
    print(f"=== END {label} ANSWER ===")
    print()
    print("HTTP:", status)
    print("read_path:", read_paths)
    print("messages:", [
        m.get("role") for m in (conversation.get("messages") or [])
    ])
    print("san_ leak:", checks["san_leak"])
    print("src_ leak:", checks["src_leak"])

    structural_pass = (
        checks["http_201"]
        and checks["conversation_readable"]
        and checks["hybrid"]
        and checks["answer_present"]
        and not checks["san_leak"]
        and not checks["src_leak"]
    )

    print(
        f"[DONE] {label} STRUCTURAL PASS"
        if structural_pass
        else f"[FAILED] {label} STRUCTURAL CHECK"
    )

    return {
        "label": label,
        "question": question,
        "answer": answer,
        "elapsed_seconds": elapsed,
        "read_paths": read_paths,
        "checks": checks,
        "structural_pass": structural_pass,
        "api_response": payload,
        "conversation": conversation,
    }


def main():
    print("============================================================")
    print("GAIA D3.2C v3 — PRODUCTION DIALOGUE CORE VALIDATION")
    print("============================================================")

    if BUNDLE.exists():
        print(f"[FAILED] bundle already exists: {BUNDLE}")
        print("[FAILED] nothing changed")
        return

    original_config = CONFIG.read_bytes()
    original_hash = sha256_bytes(original_config)

    try:
        cfg = json.loads(original_config.decode("utf-8"))
    except Exception as exc:
        print(f"[FAILED] config parse: {exc}")
        return

    if cfg.get("dialogue_read_path", "legacy") != "legacy":
        print(
            "[FAILED] expected initial dialogue_read_path=legacy; got:",
            cfg.get("dialogue_read_path"),
        )
        return

    if port_open():
        print(f"[FAILED] port {PORT} already in use")
        print("[FAILED] stop existing Gaia and rerun")
        return

    intake = ControlledIntake(create_metadata=False)
    workspace_id = intake.existing_workspace(PROJECT)
    storage_root = Path(intake.store.root)

    if not workspace_id:
        print(f"[FAILED] canonical workspace resolution failed for {PROJECT!r}")
        return

    registry = storage_root / "metadata" / "registry.json"
    sanitized = storage_root / "sanitized"

    oc_candidates = [
        storage_root / "operational_context_v0",
        storage_root / "operational_context",
        storage_root / "operational-context",
        storage_root / "oc",
    ]
    oc_paths = [path for path in oc_candidates if path.exists()]

    before = {
        "config": original_hash,
        "registry": fingerprint(registry),
        "sanitized": fingerprint(sanitized),
        "oc": {str(path): fingerprint(path) for path in oc_paths},
    }

    print("[DONE] preflight")
    print(f"project: {PROJECT}")
    print(f"workspace_id: {workspace_id}")
    print(f"storage_root: {storage_root}")
    print(f"config sha256: {original_hash}")
    print(f"registry fingerprint: {before['registry']}")
    print(f"sanitized fingerprint: {before['sanitized']}")
    print(f"OC paths detected: {[str(x) for x in oc_paths] or '<none detected>'}")

    BUNDLE.mkdir(parents=False)
    api_dir = BUNDLE / "api"
    api_dir.mkdir()

    server = None
    log_handle = None
    server_log = BUNDLE / "server.log"
    config_changed = False
    responses: list[str] = [
        "# D3.2C v3 Responses",
        "",
    ]
    evaluations = []
    all_results = []

    try:
        cfg["dialogue_read_path"] = "hybrid"
        CONFIG.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        config_changed = True
        print("[DONE] dialogue_read_path temporarily set to hybrid")

        log_handle = server_log.open("w", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        print("[RUNNING] starting Gaia server")
        server = subprocess.Popen(
            [str(ROOT / ".venv/bin/python"), "app.py"],
            cwd=ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
        )

        ready = False
        for second in range(30):
            if server.poll() is not None:
                print(
                    "[FAILED] server stopped unexpectedly "
                    f"rc={server.returncode}"
                )
                break
            try:
                with urllib.request.urlopen(
                    BASE + "/api/projects",
                    timeout=1,
                ) as response:
                    if response.status == 200:
                        ready = True
                        break
            except Exception:
                pass

            if second and second % 5 == 0:
                print(
                    "[RUNNING] waiting for Gaia server — "
                    f"elapsed {second}s"
                )
            time.sleep(1)

        if not ready:
            print("[FAILED] server did not become ready")
            return

        print("[DONE] Gaia server ready")

        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )

        print("[RUNNING] acquiring Gaia local session")
        with opener.open(BASE + "/", timeout=5) as response:
            response.read()

        if not list(jar):
            print("[FAILED] Gaia session cookie not received")
            return

        print("[DONE] Gaia session acquired")

        # Fresh conversation for each Q1–Q5.
        for label, question in QUESTIONS:
            status, created, conversation_id = create_conversation(
                opener,
                f"D3.2C v3 {label}",
            )

            if status != 201 or not conversation_id:
                print(
                    f"[FAILED] {label} conversation creation "
                    f"HTTP {status}"
                )
                evaluations.append(
                    {"label": label, "pass": False, "reason": "create_failed"}
                )
                continue

            result = run_turn(
                opener,
                conversation_id,
                label,
                question,
            )
            all_results.append(result)

            append_response(
                responses,
                label,
                question,
                result["answer"],
                result["elapsed_seconds"],
                result["read_paths"],
            )

            (api_dir / f"{label.lower()}_message.json").write_text(
                json.dumps(
                    result["api_response"],
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (api_dir / f"{label.lower()}_conversation.json").write_text(
                json.dumps(
                    result["conversation"],
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            evaluations.append(
                {
                    "label": label,
                    "structural_pass": result["structural_pass"],
                    "semantic_review": "PENDING_INDEPENDENT_REVIEW",
                }
            )

        # Real multi-turn.
        print()
        print("============================================================")
        print("[RUNNING] MULTI-TURN guest → contractor")
        print("============================================================")

        status, created, conversation_id = create_conversation(
            opener,
            "D3.2C v3 multi-turn",
        )

        if status == 201 and conversation_id:
            mt1 = run_turn(
                opener,
                conversation_id,
                "MT1",
                "Нужно ли повторное согласование принимающего при продлении гостевого пропуска?",
            )

            mt2 = run_turn(
                opener,
                conversation_id,
                "MT2",
                "А для подрядчика?",
            )

            all_results.extend([mt1, mt2])

            append_response(
                responses,
                "MULTI-TURN / Turn 1",
                mt1["question"],
                mt1["answer"],
                mt1["elapsed_seconds"],
                mt1["read_paths"],
            )
            append_response(
                responses,
                "MULTI-TURN / Turn 2",
                mt2["question"],
                mt2["answer"],
                mt2["elapsed_seconds"],
                mt2["read_paths"],
            )

            (api_dir / "multi_turn_final_conversation.json").write_text(
                json.dumps(
                    mt2["conversation"],
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            evaluations.append(
                {
                    "label": "MULTI_TURN",
                    "structural_pass": (
                        mt1["structural_pass"]
                        and mt2["structural_pass"]
                    ),
                    "semantic_review": "PENDING_INDEPENDENT_REVIEW",
                }
            )
        else:
            print(
                "[FAILED] multi-turn conversation creation "
                f"HTTP {status}"
            )
            evaluations.append(
                {
                    "label": "MULTI_TURN",
                    "structural_pass": False,
                    "semantic_review": "NOT_RUN",
                }
            )

    except Exception as exc:
        print(
            "[FAILED] validation exception:",
            f"{type(exc).__name__}: {exc}",
        )

    finally:
        print()
        print("[RUNNING] cleanup / rollback")

        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
                print("[DONE] Gaia server stopped")
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
                print("[DONE] Gaia server force-stopped")

        if log_handle is not None:
            log_handle.close()

        if config_changed:
            CONFIG.write_bytes(original_config)
            print("[DONE] config.json restored byte-for-byte")

        final_hash = sha256_bytes(CONFIG.read_bytes())

        after = {
            "config": final_hash,
            "registry": fingerprint(registry),
            "sanitized": fingerprint(sanitized),
            "oc": {str(path): fingerprint(path) for path in oc_paths},
        }

        rollback_ok = final_hash == original_hash
        sources_unchanged = (
            before["registry"] == after["registry"]
            and before["sanitized"] == after["sanitized"]
            and before["oc"] == after["oc"]
        )

        print(
            "[DONE] exact config rollback PASS"
            if rollback_ok
            else "[FAILED] CONFIG ROLLBACK HASH MISMATCH"
        )
        print(
            "[DONE] source/OC fingerprints unchanged"
            if sources_unchanged
            else "[FAILED] SOURCE/OC FINGERPRINT CHANGE"
        )

        elapsed_values = [
            x["elapsed_seconds"]
            for x in all_results
            if isinstance(x.get("elapsed_seconds"), (int, float))
        ]

        perf = {}
        if elapsed_values:
            ordered = sorted(elapsed_values)
            n = len(ordered)
            median = (
                ordered[n // 2]
                if n % 2
                else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
            )
            perf = {
                "count": n,
                "min": min(ordered),
                "max": max(ordered),
                "mean": sum(ordered) / n,
                "median": median,
            }

        manifest = {
            "schema": "gaia.d3.2c.production-dialogue.v3",
            "project": PROJECT,
            "workspace_id": workspace_id,
            "identity_chain": (
                f"{PROJECT} -> "
                "ControlledIntake.existing_workspace() -> "
                f"{workspace_id}"
            ),
            "initial_dialogue_read_path": "legacy",
            "validation_dialogue_read_path": "hybrid",
            "final_dialogue_read_path": "legacy"
            if rollback_ok
            else "UNKNOWN",
            "before_fingerprints": before,
            "after_fingerprints": after,
            "config_rollback_pass": rollback_ok,
            "source_oc_no_mutation_pass": sources_unchanged,
            "real_dialogue_endpoint": (
                "POST /api/conversations/{id}/messages"
            ),
            "real_model_calls": len(elapsed_values),
            "performance_seconds": perf,
            "no_evidence_case": "NOT_RUN_YET",
            "semantic_review": "PENDING_INDEPENDENT_REVIEW",
        }

        (BUNDLE / "RUN_MANIFEST.json").write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        (BUNDLE / "RESPONSES.md").write_text(
            "\n".join(responses) + "\n",
            encoding="utf-8",
        )

        evaluation_lines = [
            "# D3.2C v3 Evaluation",
            "",
            "Core run only. No-evidence case intentionally deferred.",
            "",
        ]

        for item in evaluations:
            evaluation_lines.append(
                f"- {item['label']}: "
                f"structural_pass={item.get('structural_pass')} "
                f"semantic_review={item.get('semantic_review')}"
            )

        evaluation_lines.extend(
            [
                "",
                f"- config rollback: {rollback_ok}",
                f"- source/OC no mutation: {sources_unchanged}",
                "- no-evidence: NOT_RUN_YET",
                "- final decision: PENDING",
                "",
            ]
        )

        (BUNDLE / "EVALUATION.md").write_text(
            "\n".join(evaluation_lines),
            encoding="utf-8",
        )

        sha_lines = []
        for path in sorted(
            p for p in BUNDLE.rglob("*")
            if p.is_file() and p.name != "SHA256SUMS"
        ):
            sha_lines.append(
                f"{sha256_bytes(path.read_bytes())}  "
                f"{path.relative_to(BUNDLE)}"
            )

        (BUNDLE / "SHA256SUMS").write_text(
            "\n".join(sha_lines) + "\n",
            encoding="utf-8",
        )

        print()
        print("============================================================")
        print("[DONE] D3.2C v3 CORE RUN FINISHED")
        print(f"[DONE] bundle: {BUNDLE}")
        print(f"[DONE] real model calls: {len(elapsed_values)}")

        if perf:
            print(
                "[DONE] latency "
                f"min={perf['min']:.1f}s "
                f"median={perf['median']:.1f}s "
                f"mean={perf['mean']:.1f}s "
                f"max={perf['max']:.1f}s"
            )

        print("[DONE] Terminal remains open")
        print("============================================================")


if __name__ == "__main__":
    main()
