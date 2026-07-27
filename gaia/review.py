"""Local review workflow for deterministic sanitized artifacts only."""
from __future__ import annotations

import json
import hashlib
import re
import time
import uuid
from datetime import datetime
from typing import Any, Callable

from .provenance import ProvenanceError, ProvenanceStore
from .runtime_diagnostics import emit as emit_runtime_diagnostic
from .runtime_diagnostics import new_correlation_id
from .storage import atomic_write_text, path_lock

ALLOWED_CATEGORIES = {"Сотрудник", "Организация", "Подразделение", "Проект", "Система", "Адрес", "Идентификатор", "Другое"}
MAX_FINDINGS = 24
REVIEW_STATUS_COMPLETED = "completed"
REVIEW_STATE_MANUAL_REVIEW_REQUIRED = "manual_review_required"


class LocalReviewError(ProvenanceError):
    def __init__(self, code: str, message: str, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics


def local_model_review(text: str) -> dict[str, Any]:
    from .module_assist import call_lm_studio_with_deadline
    prompt = "\n".join([
        "Проверь только переданный очищенный текст на остаточные сущности, которые требуют ручной проверки.",
        "Верни ровно один JSON-объект без Markdown и без пояснений.",
        "Верхний уровень содержит ровно ключи status и findings.",
        "status всегда имеет значение completed только после завершения проверки.",
        "Если находок нет, верни точно {\"status\":\"completed\",\"findings\":[]}.",
        "Пустой список допустим только в полном ответе со status=completed.",
        "Каждая находка содержит ровно category, start, end, confidence, reason_code, requires_review.",
        "start и end — реальные нулевые позиции начала и конца самостоятельного фрагмента в очищенном тексте.",
        "Не копируй примеры из этой инструкции и не создавай находки, если координаты не проверены по тексту.",
        "Слова названий категорий и псевдонимы Gaia вида «Категория-01» уже очищены и не являются находками.",
        "Находкой может быть только конкретное остаточное значение, а не заголовок, метка поля или тестовый идентификатор.",
        "category: Сотрудник, Организация, Подразделение, Проект, Система, Адрес, Идентификатор или Другое.",
        "confidence: low, medium или high. requires_review всегда true.",
        "",
        "# Очищенный текст",
        text,
    ])
    correlation_id = new_correlation_id()
    emit_runtime_diagnostic("veil", "local_model_started", correlation_id)
    started = time.monotonic()
    result = call_lm_studio_with_deadline(prompt, 20, "Ты локальный проверяющий Gaia.", task="veil_review")
    diagnostics = local_review_diagnostics(result, time.monotonic() - started, correlation_id)
    if not result.get("ok"):
        emit_runtime_diagnostic("veil", "local_model_technical_error", correlation_id, error_code=local_review_transport_code(str(result.get("status") or "")))
        raise LocalReviewError(local_review_transport_code(str(result.get("status") or "")), "Локальная дополнительная проверка не завершена.", diagnostics)
    emit_runtime_diagnostic("veil", "local_model_response_received", correlation_id)
    answer = str(result.get("answer") or "")
    if not answer.strip():
        raise LocalReviewError("local_model_empty_response", "Локальная дополнительная проверка вернула пустой ответ.", diagnostics)
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    if raw.get("done") is False or raw.get("done_reason") in {"length", "max_tokens"}:
        raise LocalReviewError("local_model_truncated_response", "Локальная дополнительная проверка вернула неполный ответ.", diagnostics)
    try:
        payload = extract_unique_json_object(answer)
    except json.JSONDecodeError as exc:
        raise LocalReviewError("local_model_invalid_json", "Локальная дополнительная проверка вернула некорректный ответ.", diagnostics) from exc
    if not is_completed_review_payload(payload):
        emit_runtime_diagnostic("veil", "payload_schema_failed", correlation_id, error_code="payload_schema_invalid")
        raise LocalReviewError("local_model_schema_failed", "Локальная дополнительная проверка вернула ответ неподходящей структуры.", diagnostics)
    emit_runtime_diagnostic("veil", "payload_received", correlation_id, findings_empty=not payload["findings"], findings_count=len(payload["findings"]))
    emit_runtime_diagnostic("veil", "payload_validation_started", correlation_id)
    try:
        validate_model_payload(payload, text)
    except ProvenanceError as exc:
        emit_runtime_diagnostic("veil", "payload_validation_failed", correlation_id, validation_result="error", error_code=validation_error_code(exc))
        raise LocalReviewError("local_model_invalid_findings", "Локальная дополнительная проверка вернула непригодные находки.", diagnostics) from exc
    emit_runtime_diagnostic("veil", "payload_validation_succeeded", correlation_id, validation_result="ok", findings_count=len(payload["findings"]))
    return payload


def extract_unique_json_object(answer: str) -> dict[str, Any]:
    stripped = answer.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, count=1, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped, count=1).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        candidates: list[dict[str, Any]] = []
        for index, char in enumerate(stripped):
            if char != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(stripped[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                candidates.append(candidate)
        if len(candidates) != 1:
            raise
        payload = candidates[0]
    if not isinstance(payload, dict):
        raise json.JSONDecodeError("Expected a JSON object", stripped, 0)
    return payload


def local_review_transport_code(status: str) -> str:
    return {"timeout": "local_model_timeout", "unavailable": "local_model_unavailable", "bad_request": "local_model_request_failed", "http_error": "local_model_request_failed"}.get(status, "local_model_request_failed")


def local_review_diagnostics(result: dict[str, Any], duration_seconds: float, trace_id: str | None = None) -> dict[str, Any]:
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
    return {"trace_id": trace_id or f"gaia-{uuid.uuid4().hex[:12]}", "stage": "local_review", "provider": str(result.get("provider") or ""), "model": str(result.get("model") or ""), "route": str(result.get("route") or "veil_review"), "request_status": str(result.get("status") or "ok"), "duration_ms": round(duration_seconds * 1000), "prompt_chars_sent": int(result.get("prompt_chars_sent") or 0), "response_chars": len(str(result.get("answer") or "")), "http_status": 200 if result.get("ok") else None, "done": raw.get("done"), "finish_reason": raw.get("done_reason") or raw.get("finish_reason"), "prompt_tokens": raw.get("prompt_eval_count"), "response_tokens": raw.get("eval_count"), "reasoning_present": bool(message.get("thinking") or message.get("reasoning_content"))}

class ReviewService:
    def __init__(self, store: ProvenanceStore, workspace_id: str, model: Callable[[str], dict[str, Any]] | None = None) -> None:
        self.store, self.workspace_id, self.model = store, workspace_id, model or local_model_review
        self.path = store.root / "metadata" / "review_state.json"
        if not self.path.exists(): atomic_write_text(self.path, "{}\n")

    def start(self, artifact_id: str) -> dict[str, Any]:
        correlation_id = new_correlation_id()
        emit_runtime_diagnostic("review_service", "review_start", correlation_id)
        item = self.store.object_metadata(self.workspace_id, artifact_id)
        if item.get("kind") != "sanitized" or not item.get("current"):
            raise ProvenanceError("Для проверки доступна только актуальная очищенная версия.")
        existing = self._read().get(artifact_id)
        if existing and existing.get("workspace_id") == self.workspace_id and existing.get("state") in {"requires_review", "review_in_progress", "ready_for_confirmation", "confirmed"}:
            return self.safe(existing, include_text=True)
        text = self._text(artifact_id)
        try:
            payload = self.model(text)
            payload_count = len(payload.get("findings", [])) if isinstance(payload, dict) and isinstance(payload.get("findings"), list) else None
            emit_runtime_diagnostic("review_service", "payload_validation_started", correlation_id, findings_count=payload_count)
            findings = validate_model_payload(payload, text)
            emit_runtime_diagnostic("review_service", "payload_validation_succeeded", correlation_id, validation_result="ok", findings_count=len(findings))
            state = "requires_review" if findings else "ready_for_confirmation"
            error_code = ""
        except LocalReviewError as exc:
            findings = []; state = REVIEW_STATE_MANUAL_REVIEW_REQUIRED; error_code = exc.code; diagnostics = exc.diagnostics
        except ProvenanceError:
            findings = []; state = REVIEW_STATE_MANUAL_REVIEW_REQUIRED; error_code = "local_model_invalid_findings"; diagnostics = {"trace_id": f"gaia-{uuid.uuid4().hex[:12]}", "stage": "local_review"}
            emit_runtime_diagnostic("review_service", "payload_validation_failed", correlation_id, validation_result="error", error_code="payload_validation_invalid")
        except Exception as exc:
            findings = []; state = REVIEW_STATE_MANUAL_REVIEW_REQUIRED; error_code = "local_check_internal_error"; diagnostics = {"trace_id": f"gaia-{uuid.uuid4().hex[:12]}", "stage": "local_review", "exception_type": type(exc).__name__}
        record = {"artifact_id": artifact_id, "workspace_id": self.workspace_id, "state": state, "findings": findings, "decisions": [], "confirmed": False, "error_code": error_code, "created_at": now()}
        if state == REVIEW_STATE_MANUAL_REVIEW_REQUIRED:
            record["trace_id"] = diagnostics["trace_id"]
            self._write_diagnostics(artifact_id, error_code, diagnostics)
        emit_runtime_diagnostic("review_service", "review_final_state", correlation_id, final_state=state, findings_count=len(findings), error_code=error_code or None)
        self._write(artifact_id, record); return self.safe(record, include_text=True)

    def prepare(self, artifact_id: str) -> dict[str, Any]:
        """Persist the unambiguous pre-check state without calling the model."""
        item = self.store.object_metadata(self.workspace_id, artifact_id)
        if item.get("kind") != "sanitized" or not item.get("current"):
            raise ProvenanceError("Для проверки доступна только актуальная очищенная версия.")
        record = {"artifact_id": artifact_id, "workspace_id": self.workspace_id, "state": "not_started", "findings": [], "decisions": [], "confirmed": False, "error_code": "", "created_at": now()}
        self._write(artifact_id, record); return self.safe(record, include_text=True)

    def get(self, artifact_id: str, include_text: bool = False) -> dict[str, Any]:
        record = self._read().get(artifact_id)
        if not record or record["workspace_id"] != self.workspace_id: raise ProvenanceError("Проверка недоступна в этом рабочем пространстве.")
        return self.safe(record, include_text)

    def get_or_start(self, artifact_id: str, include_text: bool = False) -> dict[str, Any]:
        """Return a durable review, restoring a missing state for a current version."""
        record = self._read().get(artifact_id)
        if record and record.get("workspace_id") == self.workspace_id:
            return self.safe(record, include_text)
        item = self.store.object_metadata(self.workspace_id, artifact_id)
        if item.get("kind") != "sanitized" or not item.get("current"):
            raise ProvenanceError("Проверка недоступна для этой версии.")
        return self.prepare(artifact_id)

    def create_successor(self, previous_id: str, artifact_id: str) -> dict[str, Any]:
        """Create the review state before exposing a newly sanitized version.

        A new deterministic version needs a fresh local check because positions
        and residual findings can change. Prior user decisions are retained as
        review history but never confirm the new version automatically.
        """
        previous = self._read().get(previous_id)
        if not previous or previous.get("workspace_id") != self.workspace_id:
            raise ProvenanceError("Предыдущее состояние проверки недоступно.")
        self.prepare(artifact_id)
        record = self._read()[artifact_id]
        record["carried_decisions"] = list(previous.get("decisions") or [])
        record["replaces_review"] = previous_id
        self._write(artifact_id, record)
        return self.safe(record, include_text=True)

    def decide(self, artifact_id: str, finding_id: str, decision: str, category: str = "") -> dict[str, Any]:
        if decision not in {"replace", "keep", "change_category"}: raise ProvenanceError("Некорректное решение проверки.")
        record = self._read().get(artifact_id)
        if not record or record["workspace_id"] != self.workspace_id or record["confirmed"] or record.get("state") not in {"requires_review", "review_in_progress"}: raise ProvenanceError("Решения доступны только для завершённой проверки с находками.")
        finding = next((f for f in record["findings"] if f["finding_id"] == finding_id), None)
        if not finding: raise ProvenanceError("Находка проверки не найдена.")
        if any(item["finding_id"] == finding_id for item in record["decisions"]):
            raise ProvenanceError("Решение по этой находке уже сохранено.")
        text = self._text(artifact_id)
        if _fingerprint(text[finding["start"]:finding["end"]]) != finding["expected_fingerprint"]:
            raise ProvenanceError("Находка относится к другой версии очищенного текста.")
        if decision == "change_category" and not category:
            raise ProvenanceError("Выберите новую категорию для находки.")
        if category and category not in ALLOWED_CATEGORIES: raise ProvenanceError("Выбранная категория недопустима.")
        selected_category = category or finding["category"]
        if decision == "change_category":
            finding["category"] = selected_category
        record["decisions"].append({"finding_id": finding_id, "decision": decision, "category": selected_category, "created_at": now()})
        unresolved = [item for item in record["findings"] if not any(saved["finding_id"] == item["finding_id"] for saved in record["decisions"])]
        record["state"] = "review_in_progress" if unresolved else "ready_for_confirmation"
        self._write(artifact_id, record); return self.safe(record)

    def confirm(self, artifact_id: str) -> str:
        record = self._read().get(artifact_id)
        item = self.store.object_metadata(self.workspace_id, artifact_id)
        if not record or record["workspace_id"] != self.workspace_id or not item.get("current"):
            raise ProvenanceError("Нельзя подтвердить неактуальную версию.")
        if record.get("confirmed"):
            return self._text(artifact_id)
        if record.get("state") not in {"ready_for_confirmation", REVIEW_STATE_MANUAL_REVIEW_REQUIRED}:
            raise ProvenanceError("Подтверждение доступно только после завершения локальной проверки.")
        record["confirmation_method"] = "manual" if record.get("state") == REVIEW_STATE_MANUAL_REVIEW_REQUIRED else "veil_review"
        record["confirmed"] = True; record["state"] = "confirmed"; record["confirmed_at"] = now(); self._write(artifact_id, record)
        return self._text(artifact_id)

    def safe(self, record: dict[str, Any], include_text: bool = False) -> dict[str, Any]:
        result = {k: record[k] for k in ("artifact_id", "state", "findings", "decisions", "confirmed")}
        result["error_code"] = record.get("error_code", "")
        result["trace_id"] = record.get("trace_id", "")
        result["confirmation_method"] = record.get("confirmation_method", "")
        result["carried_decisions"] = list(record.get("carried_decisions") or [])
        if include_text: result["cleaned_text"] = self._text(record["artifact_id"])
        return result

    def _text(self, artifact_id: str) -> str:
        return (self.store.root / "sanitized" / self.workspace_id / f"{artifact_id}.txt").read_text(encoding="utf-8")
    def _read(self) -> dict[str, Any]: return json.loads(self.path.read_text(encoding="utf-8"))
    def _write(self, artifact_id: str, record: dict[str, Any]) -> None:
        with path_lock(self.path):
            payload = self._read(); payload[artifact_id] = record; atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def _write_diagnostics(self, artifact_id: str, error_code: str, diagnostics: dict[str, Any]) -> None:
        path = self.store.root / "metadata" / "review_diagnostics.json"
        with path_lock(path):
            existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            existing.append({**diagnostics, "artifact_id": artifact_id, "workspace": hashlib.sha256(self.workspace_id.encode("utf-8")).hexdigest()[:12], "error_code": error_code})
            atomic_write_text(path, json.dumps(existing[-100:], ensure_ascii=False, indent=2) + "\n")

def validate_model_payload(payload: Any, text: str | int) -> list[dict[str, Any]]:
    text_length = len(text) if isinstance(text, str) else text
    if not is_completed_review_payload(payload):
        raise ProvenanceError("Некорректный ответ локальной проверки.")
    result=[]; used=[]
    for index, item in enumerate(payload["findings"], 1):
        if not isinstance(item, dict) or set(item) != {"category","start","end","confidence","reason_code","requires_review"}: raise ProvenanceError("Некорректный ответ локальной проверки.")
        start,end=item["start"],item["end"]
        if item["category"] not in ALLOWED_CATEGORIES or not isinstance(start,int) or not isinstance(end,int) or not 0 <= start < end <= text_length or any(start < b and end > a for a,b in used): raise ProvenanceError("Некорректные координаты локальной проверки.")
        if item["confidence"] not in {"low","medium","high"} or not isinstance(item["reason_code"],str) or len(item["reason_code"]) > 48 or item["requires_review"] is not True: raise ProvenanceError("Некорректный ответ локальной проверки.")
        expected_text = text[start:end] if isinstance(text, str) else ""
        if isinstance(text, str) and (not _is_complete_fragment(text, start, end) or _overlaps_pseudonym(text, start, end)):
            raise ProvenanceError("Координаты локальной проверки указывают не на самостоятельную находку.")
        if _is_pseudonym_fragment(expected_text):
            raise ProvenanceError("Локальная проверка не может предлагать уже созданный псевдоним.")
        used.append((start,end)); result.append({**item,"finding_id":f"model-{index}","expected_fingerprint":_fingerprint(expected_text)})
    return result


def is_completed_review_payload(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and set(payload) == {"status", "findings"}
        and payload.get("status") == REVIEW_STATUS_COMPLETED
        and isinstance(payload.get("findings"), list)
        and len(payload["findings"]) <= MAX_FINDINGS
    )


def validation_error_code(exc: ProvenanceError) -> str:
    """Map existing validator failures to stable, content-free diagnostic codes."""
    if str(exc).startswith(("Некорректные координаты", "Координаты локальной проверки")):
        return "payload_coordinates_invalid"
    return "payload_validation_invalid"

def _is_pseudonym_fragment(value: str) -> bool:
    return bool(re.fullmatch(r"(?:" + "|".join(re.escape(category) for category in ALLOWED_CATEGORIES) + r")-\d{2,}", value))

def _is_complete_fragment(text: str, start: int, end: int) -> bool:
    return (start == 0 or not _is_word_char(text[start - 1])) and (end == len(text) or not _is_word_char(text[end]))

def _is_word_char(value: str) -> bool:
    return bool(re.match(r"\w", value)) or value == "-"

def _overlaps_pseudonym(text: str, start: int, end: int) -> bool:
    pattern = r"(?:" + "|".join(re.escape(category) for category in ALLOWED_CATEGORIES) + r")-\d{2,}"
    return any(start < match.end() and end > match.start() for match in re.finditer(pattern, text))

def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
def now() -> str: return datetime.now().isoformat(timespec="seconds")
