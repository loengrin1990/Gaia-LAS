"""Local review workflow for deterministic sanitized artifacts only."""
from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime
from typing import Any, Callable

from .provenance import ProvenanceError, ProvenanceStore
from .storage import atomic_write_text, path_lock

ALLOWED_CATEGORIES = {"Сотрудник", "Организация", "Подразделение", "Проект", "Система", "Адрес", "Идентификатор", "Другое"}
MAX_FINDINGS = 24

def local_model_review(text: str) -> dict[str, Any]:
    from .module_assist import call_lm_studio_with_deadline
    prompt = "Верни только JSON: {\"findings\":[{\"category\":\"Сотрудник\",\"start\":0,\"end\":1,\"confidence\":\"medium\",\"reason_code\":\"residual_candidate\",\"requires_review\":true}]}. Анализируй только очищенный текст.\n\n" + text
    result = call_lm_studio_with_deadline(prompt, 8, "Ты локальный проверяющий Gaia.", task="veil_review")
    if not result.get("ok"):
        raise ProvenanceError("Локальная дополнительная проверка не завершена.")
    try:
        return json.loads(str(result.get("answer") or ""))
    except json.JSONDecodeError as exc:
        raise ProvenanceError("Локальная дополнительная проверка вернула некорректный ответ.") from exc

class ReviewService:
    def __init__(self, store: ProvenanceStore, workspace_id: str, model: Callable[[str], dict[str, Any]] | None = None) -> None:
        self.store, self.workspace_id, self.model = store, workspace_id, model or local_model_review
        self.path = store.root / "metadata" / "review_state.json"
        if not self.path.exists(): atomic_write_text(self.path, "{}\n")

    def start(self, artifact_id: str) -> dict[str, Any]:
        item = self.store.object_metadata(self.workspace_id, artifact_id)
        if item.get("kind") != "sanitized" or not item.get("current"):
            raise ProvenanceError("Для проверки доступна только актуальная очищенная версия.")
        existing = self._read().get(artifact_id)
        if existing and existing.get("workspace_id") == self.workspace_id and existing.get("state") in {"requires_review", "review_in_progress", "ready_for_confirmation", "confirmed"}:
            return self.safe(existing, include_text=True)
        text = self._text(artifact_id)
        try:
            findings = validate_model_payload(self.model(text), text)
            state = "requires_review" if findings else "ready_for_confirmation"
            error_code = ""
        except Exception:
            findings = []; state = "review_error"; error_code = "local_check_failed"
        record = {"artifact_id": artifact_id, "workspace_id": self.workspace_id, "state": state, "findings": findings, "decisions": [], "confirmed": False, "error_code": error_code, "created_at": now()}
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
        if record.get("state") != "ready_for_confirmation":
            raise ProvenanceError("Подтверждение доступно только после завершения локальной проверки.")
        record["confirmed"] = True; record["state"] = "confirmed"; record["confirmed_at"] = now(); self._write(artifact_id, record)
        return self._text(artifact_id)

    def safe(self, record: dict[str, Any], include_text: bool = False) -> dict[str, Any]:
        result = {k: record[k] for k in ("artifact_id", "state", "findings", "decisions", "confirmed")}
        result["error_code"] = record.get("error_code", "")
        result["carried_decisions"] = list(record.get("carried_decisions") or [])
        if include_text: result["cleaned_text"] = self._text(record["artifact_id"])
        return result

    def _text(self, artifact_id: str) -> str:
        return (self.store.root / "sanitized" / self.workspace_id / f"{artifact_id}.txt").read_text(encoding="utf-8")
    def _read(self) -> dict[str, Any]: return json.loads(self.path.read_text(encoding="utf-8"))
    def _write(self, artifact_id: str, record: dict[str, Any]) -> None:
        with path_lock(self.path):
            payload = self._read(); payload[artifact_id] = record; atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

def validate_model_payload(payload: Any, text: str | int) -> list[dict[str, Any]]:
    text_length = len(text) if isinstance(text, str) else text
    if not isinstance(payload, dict) or set(payload) != {"findings"} or not isinstance(payload["findings"], list) or len(payload["findings"]) > MAX_FINDINGS:
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
