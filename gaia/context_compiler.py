"""Compile confirmed sanitized material into reviewable provenance context."""
from __future__ import annotations

import json
from typing import Any, Callable

from .provenance import ProvenanceError, ProvenanceStore
from .review import ReviewService
from .local_llm import TASK_CONTEXT_COMPILER

COMPILER_VERSION = "context-v1"
PROMPT_SCHEMA_VERSION = "context-schema-v1"
TYPES = {"requirement", "decision", "risk", "open_question", "action"}
OPTIONAL = {"actor_ref", "deadline", "status", "priority", "reason", "consequence"}
RELATIONS_FIELD = "relations"
MAX_CANDIDATES = 32
MAX_RESULT_SIZE = 48_000
MAX_INPUT_SIZE = 120_000

class ContextCompileError(ProvenanceError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message); self.code = code

def local_context_model(text: str) -> dict[str, Any]:
    from .module_assist import call_lm_studio_with_deadline
    prompt = (
        "Верни только объект JSON без Markdown. Единственный допустимый ключ верхнего уровня: candidates. "
        "Каждый элемент candidates обязан иметь ровно поля type, title, statement, block, confidence, requires_review; "
        "type только requirement, decision, risk, open_question или action; block имеет только start и end с координатами очищенного текста. "
        "Не используй ключи requirement, solution, risk, question или action как замену структуры кандидата. "
        "Пример: {\"candidates\":[{\"type\":\"requirement\",\"title\":\"Проверка\",\"statement\":\"Проверить материал.\",\"block\":{\"start\":0,\"end\":10},\"confidence\":\"high\",\"requires_review\":true}]}. "
        "Извлеки только явно сказанные требования, решения, риски, вопросы и действия из очищенного текста.\n\n" + text
    )
    result = call_lm_studio_with_deadline(prompt, 45, "Ты локальный компилятор проектного контекста Gaia.", task=TASK_CONTEXT_COMPILER)
    if not result.get("ok"): raise ContextCompileError("local_model_unavailable", "Локальный компилятор контекста недоступен.")
    try: return json.loads(str(result.get("answer") or ""))
    except json.JSONDecodeError as exc: raise ContextCompileError("local_model_invalid", "Локальный компилятор вернул некорректный ответ.") from exc

class ContextCompiler:
    def __init__(self, store: ProvenanceStore, workspace_id: str, model: Callable[[str], dict[str, Any]] | None = None) -> None:
        self.store, self.workspace_id, self.model = store, workspace_id, model or local_context_model

    def compile(self, sanitized_id: str, compiler_version: str = COMPILER_VERSION) -> list[dict[str, Any]]:
        item = self.store.object_metadata(self.workspace_id, sanitized_id)
        if item.get("kind") != "sanitized": raise ContextCompileError("material_unavailable", "Очищенный материал недоступен в выбранном рабочем пространстве.")
        if not item.get("current"): raise ContextCompileError("stale_version", "Эта версия больше не актуальна.")
        review = ReviewService(self.store, self.workspace_id).get(sanitized_id)
        if not review.get("confirmed"): raise ContextCompileError("material_not_confirmed", "Сначала подтвердите очищенный материал.")
        extraction_id = (item.get("parents") or [""])[0]
        extraction = self.store.object_metadata(self.workspace_id, extraction_id)
        self.store.source_metadata(self.workspace_id, (extraction.get("parents") or [""])[0])
        prior = [x for x in self.store._registry()["objects"].values() if x.get("kind") == "context" and x.get("workspace_id") == self.workspace_id and x.get("parents") == [sanitized_id] and x.get("compiler_version") == compiler_version]
        if prior: return [dict(x) for x in prior]
        text = (self.store.root / "sanitized" / self.workspace_id / f"{sanitized_id}.txt").read_text(encoding="utf-8")
        if len(text) > MAX_INPUT_SIZE:
            raise ProvenanceError("Подтверждённый материал превышает допустимый объём компиляции.")
        try:
            candidates = validate_candidates(self.model(text), len(text))
        except ContextCompileError:
            raise
        except ProvenanceError:
            raise ContextCompileError("local_model_invalid", "Локальный компилятор вернул результат, который не прошёл проверку.")
        except Exception as exc:
            raise ContextCompileError("local_model_unavailable", "Локальный компилятор контекста недоступен.") from exc
        result=[]
        for candidate in candidates:
            duplicate = self._exact_duplicate(candidate)
            if duplicate:
                sources = list(duplicate.get("source_links") or [])
                if sanitized_id not in sources:
                    sources.append(sanitized_id); self.store._update(duplicate["id"], source_links=sources)
                result.append(self.store.object_metadata(self.workspace_id, duplicate["id"])); continue
            values = {key: candidate.get(key) for key in OPTIONAL - {"status"} if key in candidate}
            if "status" in candidate:
                values["explicit_status"] = candidate["status"]
            if RELATIONS_FIELD in candidate:
                values["proposed_relations"] = candidate[RELATIONS_FIELD]
            record = self.store._record(self.store._id("ctx"), self.workspace_id, "context", item_type=candidate["type"], parents=[sanitized_id], source_links=[sanitized_id], block_links=[candidate["block"]], title=candidate["title"], statement=candidate["statement"], status="requires_review", confidence=candidate["confidence"], requires_review=True, compiler_version=compiler_version, prompt_schema_version=PROMPT_SCHEMA_VERSION, model_route="local_loopback", version=1, supersedes_id="", confirmation_status="pending", relation_ids=[], current=True, export_allowed=False, **values)
            self._mark_possible_duplicates(record)
            self._mark_conflicts(record)
            self.store._add(record); result.append(record)
        return result

    def _exact_duplicate(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        norm = candidate["statement"].strip().casefold()
        return next((x for x in self.store._registry()["objects"].values() if x.get("kind") == "context" and x.get("workspace_id") == self.workspace_id and x.get("item_type") == candidate["type"] and str(x.get("statement", "")).strip().casefold() == norm), None)

    def _mark_conflicts(self, record: dict[str, Any]) -> None:
        if record["item_type"] != "decision": return
        for old in self.store._registry()["objects"].values():
            if old.get("workspace_id") == self.workspace_id and old.get("kind") == "context" and old.get("item_type") == "decision" and old.get("title") == record["title"] and old.get("statement") != record["statement"]:
                # A prior confirmed decision remains current until an explicit user choice.
                record["status"] = "conflicted"; record["relation_ids"].append(old["id"])
                self.store._update(old["id"], relation_ids=list(set(old.get("relation_ids", []) + [record["id"]])))

    def _mark_possible_duplicates(self, record: dict[str, Any]) -> None:
        """Link similar titles without silently consolidating their meaning."""
        for old in self.store._registry()["objects"].values():
            if (old.get("workspace_id") == self.workspace_id and old.get("kind") == "context"
                    and old.get("item_type") == record["item_type"]
                    and old.get("title", "").strip().casefold() == record["title"].strip().casefold()
                    and old.get("statement") != record["statement"]):
                record["relation_ids"].append(old["id"])
                self.store._update(old["id"], relation_ids=list(set(old.get("relation_ids", []) + [record["id"]])))

class ContextService:
    def __init__(self, store: ProvenanceStore, workspace_id: str) -> None: self.store, self.workspace_id = store, workspace_id
    def list(self) -> list[dict[str, Any]]: return [dict(x) for x in self.store._registry()["objects"].values() if x.get("kind") == "context" and x.get("workspace_id") == self.workspace_id]
    def get(self, context_id: str) -> dict[str, Any]: return self.store._object(self.workspace_id, context_id, "context")
    def decide(self, context_id: str, decision: str, title: str = "", statement: str = "") -> dict[str, Any]:
        item = self.get(context_id)
        if decision == "confirm": self.store._update(context_id, status="confirmed", confirmation_status="confirmed", requires_review=False); return self.get(context_id)
        if decision == "reject": self.store._update(context_id, status="rejected", confirmation_status="rejected", current=False); return self.get(context_id)
        if decision == "edit":
            if not title.strip() or not statement.strip(): raise ProvenanceError("Укажите заголовок и содержание новой версии.")
            self.store._update(context_id, current=False, status="superseded")
            values = {key:value for key,value in item.items() if key not in {"id", "context_item_id", "workspace_id", "kind", "created_at", "schema_version"}}
            new_id = self.store._id("ctx")
            values.update({"title": title.strip(), "statement": statement.strip(), "version": int(item.get("version",1))+1, "supersedes_id": context_id, "status":"requires_review", "confirmation_status":"pending", "current":True})
            record = self.store._record(new_id, self.workspace_id, "context", **values)
            self.store._add(record); return record
        raise ProvenanceError("Некорректное решение по кандидату.")

    def mark_duplicate(self, context_id: str, target_id: str) -> dict[str, Any]:
        item, target = self.get(context_id), self.get(target_id)
        if context_id == target_id or item.get("item_type") != target.get("item_type"):
            raise ProvenanceError("Повтор можно отметить только у другого элемента того же типа.")
        sources = list(target.get("source_links") or [])
        for source in item.get("source_links") or []:
            if source not in sources:
                sources.append(source)
        self.store._update(target_id, source_links=sources)
        self.store._update(context_id, status="rejected", confirmation_status="duplicate", current=False,
                           relation_ids=list(set(item.get("relation_ids", []) + [target_id])), duplicate_of=target_id)
        return self.get(context_id)

    def resolve_conflict(self, context_id: str, resolution: str) -> dict[str, Any]:
        item = self.get(context_id)
        related = [self.get(item_id) for item_id in item.get("relation_ids", [])]
        if not related:
            raise ProvenanceError("У кандидата нет отмеченного противоречия.")
        if resolution == "keep_open":
            self.store._update(context_id, status="conflicted", confirmation_status="pending")
        elif resolution == "choose_current":
            for other in related:
                self.store._update(other["id"], status="superseded", current=False)
            self.store._update(context_id, status="confirmed", confirmation_status="confirmed", requires_review=False, current=True)
        elif resolution == "keep_both":
            for other in related:
                self.store._update(other["id"], status="confirmed", confirmation_status="confirmed", requires_review=False, current=True)
            self.store._update(context_id, status="confirmed", confirmation_status="confirmed", requires_review=False, current=True)
        else:
            raise ProvenanceError("Некорректное решение по противоречию.")
        return self.get(context_id)
    def summary(self, filters: dict[str, str] | None = None) -> dict[str, list[dict[str, Any]]]:
        filters=filters or {}; sections={key:[] for key in TYPES}
        for item in self.list():
            if item.get("status") != "confirmed" or not item.get("current"): continue
            if filters.get("type") and filters["type"] != item.get("item_type"): continue
            if filters.get("status") and filters["status"] != item.get("status"): continue
            if filters.get("conflict") == "true" and not item.get("relation_ids"): continue
            if filters.get("conflict") == "false" and item.get("relation_ids"): continue
            if filters.get("deadline") == "true" and not item.get("deadline"): continue
            if filters.get("actor") == "true" and not item.get("actor_ref"): continue
            sections[item["item_type"]].append({key:item.get(key) for key in ("title","statement","status","actor_ref","deadline","updated_at","source_links","relation_ids")})
        return sections

def validate_candidates(payload: Any, length: int) -> list[dict[str, Any]]:
    if (not isinstance(payload, dict) or set(payload) != {"candidates"}
            or not isinstance(payload["candidates"], list) or len(payload["candidates"]) > MAX_CANDIDATES
            or len(json.dumps(payload, ensure_ascii=False)) > MAX_RESULT_SIZE):
        raise ProvenanceError("Некорректный результат компилятора.")
    result=[]
    required={"type","title","statement","block","confidence","requires_review"}
    for item in payload["candidates"]:
        if not isinstance(item,dict) or not required.issubset(item) or set(item)-required-OPTIONAL-{RELATIONS_FIELD}: raise ProvenanceError("Некорректный результат компилятора.")
        if item["type"] not in TYPES or not isinstance(item["title"],str) or not 1<=len(item["title"])<=160 or not isinstance(item["statement"],str) or not 1<=len(item["statement"])<=1200 or item["confidence"] not in {"low","medium","high"} or item["requires_review"] is not True: raise ProvenanceError("Некорректный результат компилятора.")
        block=item["block"]
        if not isinstance(block,dict) or set(block)!={"start","end"} or not isinstance(block["start"],int) or not isinstance(block["end"],int) or not 0<=block["start"]<block["end"]<=length: raise ProvenanceError("Некорректная ссылка на блок.")
        for field in OPTIONAL:
            if field in item and not isinstance(item[field],str): raise ProvenanceError("Некорректный результат компилятора.")
        if RELATIONS_FIELD in item and (not isinstance(item[RELATIONS_FIELD], list) or len(item[RELATIONS_FIELD]) > 8 or any(not isinstance(value, str) or not value.strip() or len(value) > 160 for value in item[RELATIONS_FIELD])):
            raise ProvenanceError("Некорректный результат компилятора.")
        result.append(dict(item))
    return result
