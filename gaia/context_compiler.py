"""Compile confirmed sanitized material into reviewable provenance context."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Callable

from .provenance import ProvenanceError, ProvenanceStore
from .review import ReviewService
from .local_llm import TASK_CONTEXT_COMPILER, resolve_route
from .context_chunking import ChunkLimitError, ContextChunk, split_context
from .storage import atomic_write_text, path_lock
from .runtime_diagnostics import emit as emit_runtime_diagnostic

COMPILER_VERSION = "context-v2"
PROMPT_SCHEMA_VERSION = "context-schema-v2"
TYPES = {"requirement", "decision", "risk", "open_question", "action"}
OPTIONAL = {"actor_ref", "deadline", "status", "priority", "reason", "consequence"}
RELATIONS_FIELD = "relations"
MAX_CANDIDATES = 32
MAX_TOTAL_CANDIDATES = 512
MAX_RESULT_SIZE = 48_000
MAX_INPUT_SIZE = 250_000

class ContextCompileError(ProvenanceError):
    def __init__(self, code: str, message: str, diagnostic_code: str = "") -> None:
        super().__init__(message); self.code, self.diagnostic_code = code, diagnostic_code


class CandidateValidationError(ProvenanceError):
    def __init__(self, diagnostic_code: str) -> None:
        super().__init__("Некорректный результат компилятора.")
        self.diagnostic_code = diagnostic_code

def context_response_schema(max_candidates: int) -> dict[str, Any]:
    properties = {"type": {"enum": sorted(TYPES)}, "title": {"type": "string"}, "statement": {"type": "string"}, "block": {"type": "object", "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}}, "required": ["start", "end"], "additionalProperties": False}, "confidence": {"enum": ["low", "medium", "high"]}, "requires_review": {"const": True}}
    properties.update({key: {"type": "string"} for key in OPTIONAL})
    properties[RELATIONS_FIELD] = {"type": "array", "items": {"type": "string"}, "maxItems": 8}
    return {"type": "object", "properties": {"candidates": {"type": "array", "maxItems": max_candidates, "items": {"type": "object", "properties": properties, "required": ["type", "title", "statement", "block", "confidence", "requires_review"], "additionalProperties": False}}}, "required": ["candidates"], "additionalProperties": False}


def local_context_model(text: str) -> dict[str, Any]:
    route = resolve_route(TASK_CONTEXT_COMPILER)
    schema = context_response_schema(int(route.get("max_candidates_per_chunk", 16))) if route.get("structured_output", "schema") == "schema" else None
    prompt = (
        "Верни только объект JSON без Markdown. Единственный допустимый ключ верхнего уровня: candidates. "
        "Обязательные поля каждого кандидата: type, title, statement, block, confidence, requires_review. "
        "Разрешённые необязательные поля: actor_ref, deadline, status, priority, reason, consequence, relations. "
        "Добавляй необязательное поле только если его значение прямо названо в текущем фрагменте; не придумывай ответственного, срок, статус или приоритет. "
        "Сопоставление optional metadata: «[Координатор-Север] должен согласовать до 15 сентября 2026 года. Статус: назначено. Приоритет: высокий.» "
        "даёт actor_ref «[Координатор-Север]», deadline «15 сентября 2026 года», status «назначено», priority «высокий». "
        "«владельцем процесса назначен [Координатор-Орбита]» даёт actor_ref «[Координатор-Орбита]»; "
        "«до 1 октября 2026 года» даёт deadline «1 октября 2026 года»; "
        "«Ответственный за контроль: [Инженер-Север]» даёт actor_ref «[Инженер-Север]». "
        "type: только requirement, decision, risk, open_question или action. Для русского «Решение» всегда используй type decision; type solution запрещён. confidence: только строка low, medium или high; никогда не число. "
        "requires_review: только JSON boolean true, ключ пишется только requires_review с подчёркиванием. "
        "block: только объект {\"start\":целое,\"end\":целое} с координатами очищенного текста, 0 <= start < end <= длина текста. "
        "Не используй русские enum, пробелы в ключах или ключи requirement, solution, risk, question, action как замену структуры кандидата. "
        "Пример: {\"candidates\":[{\"type\":\"requirement\",\"title\":\"Проверка\",\"statement\":\"Проверить материал.\",\"block\":{\"start\":0,\"end\":10},\"confidence\":\"high\",\"requires_review\":true}]}. "
        "Извлеки только явно сказанные требования, решения, риски, вопросы и действия из очищенного текста; не добавляй предположений.\n\n" + text
    )
    from .module_assist import call_lm_studio_with_deadline
    result = call_lm_studio_with_deadline(prompt, int(route.get("timeout_seconds", 120)), "Ты локальный компилятор проектного контекста Gaia.", task=TASK_CONTEXT_COMPILER, response_schema=schema)
    emit_runtime_diagnostic("context_compile_model", "context_compile_model", f"gaia-{uuid.uuid4().hex[:12]}", route=TASK_CONTEXT_COMPILER, model=str(route.get("model", "")), prompt_chars=len(text), num_ctx=int(route.get("context_length", 0)), num_predict=int(route.get("max_tokens", 0)), prompt_eval_count=result.get("prompt_eval_count"), eval_count=result.get("eval_count"), done_reason=str(result.get("done_reason") or ""), total_duration=result.get("total_duration"), load_duration=result.get("load_duration"), prompt_eval_duration=result.get("prompt_eval_duration"), eval_duration=result.get("eval_duration"), validation="received")
    if not result.get("ok"):
        code = "timeout" if result.get("status") == "timeout" else "provider_unavailable"
        raise ContextCompileError("local_model_unavailable", "Локальный компилятор контекста недоступен.", code)
    answer = str(result.get("answer") or "")
    if not answer.strip():
        raise ContextCompileError("local_model_invalid", "Локальный компилятор вернул пустой ответ.", "empty_response")
    try: return json.loads(answer)
    except json.JSONDecodeError as exc:
        ceiling = int(route.get("max_tokens", 2400))
        code = "output_truncated" if result.get("done_reason") in {"length", "max_tokens"} or int(result.get("eval_count") or 0) >= ceiling else "json_parse"
        raise ContextCompileError("local_model_invalid", "Локальный компилятор вернул некорректный ответ.", code) from exc

class ContextCompiler:
    def __init__(self, store: ProvenanceStore, workspace_id: str, model: Callable[[str], dict[str, Any]] | None = None) -> None:
        self.store, self.workspace_id, self.model = store, workspace_id, model or local_context_model

    def compile(self, sanitized_id: str, compiler_version: str = COMPILER_VERSION, cancel_event: Any = None, progress: Callable[[int, int, int], None] | None = None) -> list[dict[str, Any]]:
        self._model_attempts = 0
        item = self.preflight(sanitized_id)
        extraction_id = (item.get("parents") or [""])[0]
        extraction = self.store.object_metadata(self.workspace_id, extraction_id)
        self.store.source_metadata(self.workspace_id, (extraction.get("parents") or [""])[0])
        receipt = self._receipt(sanitized_id)
        if receipt and receipt.get("status") == "complete" and receipt.get("compiler_version") == compiler_version:
            return self._restore_receipt(receipt, sanitized_id, compiler_version)
        text = (self.store.root / "sanitized" / self.workspace_id / f"{sanitized_id}.txt").read_text(encoding="utf-8")
        route = self._route()
        if len(text) > int(route["max_input_chars"]):
            raise ContextCompileError("context_limit", "Материал слишком большой для одной сборки проектного контекста. Разделите его по главам или разделам и повторите обработку. Данные не изменены.")
        try: chunks = split_context(text, int(route["chunk_char_limit"]), int(route["chunk_max_units"]), int(route["chunk_overlap_chars"]), int(route["max_chunks"]))
        except ChunkLimitError as exc: raise ContextCompileError("chunk_limit", "Материал слишком большой для одной сборки проектного контекста. Разделите его по главам или разделам и повторите обработку.") from exc
        candidates=[]
        for position, chunk in enumerate(chunks, 1):
            if cancel_event is not None and cancel_event.is_set(): raise ContextCompileError("cancelled", "Сборка контекста отменена. Данные не изменены.")
            local = self._compile_chunk(chunk.text, route, cancel_event)
            for candidate in local:
                candidate["block"] = {"start": chunk.start + candidate["block"]["start"], "end": chunk.start + candidate["block"]["end"]}
            candidates.extend(local)
            if len(candidates) > int(route["max_total_candidates"]): raise ContextCompileError("total_candidate_limit", "Слишком много элементов контекста. Данные не изменены.")
            if progress: progress(position, len(chunks), len(candidates))
        candidates = _deduplicate_candidates(candidates)
        if cancel_event is not None and cancel_event.is_set(): raise ContextCompileError("cancelled", "Сборка контекста отменена. Данные не изменены.")
        result = self._persist_all(item, sanitized_id, candidates, compiler_version, route, cancel_event)
        self._write_receipt(sanitized_id, result, len(chunks), compiler_version, route)
        return result

    def preflight(self, sanitized_id: str) -> dict[str, Any]:
        item = self.store.object_metadata(self.workspace_id, sanitized_id)
        if item.get("kind") != "sanitized": raise ContextCompileError("material_unavailable", "Очищенный материал недоступен в выбранном рабочем пространстве.")
        if not item.get("current"): raise ContextCompileError("stale_version", "Эта версия больше не актуальна.")
        review = ReviewService(self.store, self.workspace_id).get(sanitized_id)
        if not review.get("confirmed"): raise ContextCompileError("material_not_confirmed", "Сначала подтвердите очищенный материал.")
        return item

    def _route(self) -> dict[str, Any]:
        route = resolve_route(TASK_CONTEXT_COMPILER)
        defaults = {"prompt_char_limit": 9000, "max_tokens": 2400, "context_length": 32768, "timeout_seconds": 120, "chunk_char_limit": 4000, "chunk_max_units": 12, "chunk_overlap_chars": 250, "max_candidates_per_chunk": 16, "max_total_candidates": 512, "max_input_chars": MAX_INPUT_SIZE, "max_chunks": 80, "retry_count": 1, "job_timeout_seconds": 1800, "max_model_attempts": 256}
        return {**defaults, **route}

    def _compile_chunk(self, text: str, route: dict[str, Any], cancel_event: Any, depth: int = 0) -> list[dict[str, Any]]:
        failures: list[ContextCompileError] = []
        self._model_attempts += 1
        if self._model_attempts > int(route["max_model_attempts"]):
            raise ContextCompileError("model_attempt_limit", "Превышен безопасный предел попыток сборки контекста. Данные не изменены.")
        for _ in range(int(route["retry_count"]) + 1):
            if cancel_event is not None and cancel_event.is_set(): raise ContextCompileError("cancelled", "Сборка контекста отменена. Данные не изменены.")
            try:
                local = validate_candidates(self.model(text), len(text), int(route["max_candidates_per_chunk"]))
                if len(local) >= int(route["max_candidates_per_chunk"]):
                    raise CandidateValidationError("schema_candidates")
                return local
            except CandidateValidationError as exc:
                failures.append(ContextCompileError("local_model_invalid", "Локальный компилятор вернул результат, который не прошёл проверку.", exc.diagnostic_code))
            except ContextCompileError as exc:
                if exc.diagnostic_code in {"empty_response", "output_truncated", "json_parse", "schema_top_level", "schema_candidates", "schema_required_fields", "schema_unknown_field", "block_coordinates", "result_too_large"}:
                    failures.append(exc)
                else: raise
            except Exception as exc:
                failures.append(ContextCompileError("local_model_unavailable", "Локальный компилятор контекста недоступен.", "provider_unavailable"))
        minimum, max_depth = 500, 4
        if len(text) <= minimum or depth >= max_depth:
            raise failures[-1]
        midpoint = len(text) // 2
        boundaries = [text.rfind("\n\n", 0, midpoint), text.find("\n\n", midpoint)]
        cut = max((point for point in boundaries if point > 0), key=lambda point: -abs(point - midpoint), default=midpoint)
        if cut <= 0 or cut >= len(text): cut = midpoint
        halves = [ContextChunk(0, text[:cut], 0, cut, "retry"), ContextChunk(1, text[cut:], cut, len(text), "retry")]
        result: list[dict[str, Any]] = []
        for half in halves:
            for candidate in self._compile_chunk(half.text, route, cancel_event, depth + 1):
                candidate["block"] = {"start": half.start + candidate["block"]["start"], "end": half.start + candidate["block"]["end"]}
                result.append(candidate)
        return result

    def _persist_all(self, item: dict[str, Any], sanitized_id: str, candidates: list[dict[str, Any]], compiler_version: str, route: dict[str, Any], cancel_event: Any) -> list[dict[str, Any]]:
        if cancel_event is not None and cancel_event.is_set(): raise ContextCompileError("cancelled", "Сборка контекста отменена. Данные не изменены.")
        with path_lock(self.store.registry_path):
            registry = self.store._registry(); objects = registry["objects"]; result: list[dict[str, Any]] = []
            for candidate in candidates:
                duplicate = next((x for x in objects.values() if x.get("kind") == "context" and x.get("workspace_id") == self.workspace_id and x.get("item_type") == candidate["type"] and str(x.get("statement", "")).strip().casefold() == candidate["statement"].strip().casefold()), None)
                if duplicate:
                    duplicate = dict(duplicate); sources = list(duplicate.get("source_links") or [])
                    if sanitized_id not in sources: sources.append(sanitized_id)
                    duplicate["source_links"] = sources; objects[duplicate["id"]] = duplicate; result.append(dict(duplicate)); continue
                values = {key: candidate.get(key) for key in OPTIONAL - {"status"} if key in candidate}
                if "status" in candidate: values["explicit_status"] = candidate["status"]
                if RELATIONS_FIELD in candidate: values["proposed_relations"] = candidate[RELATIONS_FIELD]
                record = self.store._record(self.store._id("ctx"), self.workspace_id, "context", item_type=candidate["type"], parents=[sanitized_id], source_links=[sanitized_id], block_links=candidate.get("block_links", [candidate["block"]]), title=candidate["title"], statement=candidate["statement"], status="requires_review", confidence=candidate["confidence"], requires_review=True, compiler_version=compiler_version, prompt_schema_version=PROMPT_SCHEMA_VERSION, model_route=str(route["provider"]), model_name=str(route["model"]), version=1, supersedes_id="", confirmation_status="pending", relation_ids=[], current=True, export_allowed=False, **values)
                for old in objects.values():
                    if old.get("workspace_id") != self.workspace_id or old.get("kind") != "context": continue
                    if old.get("item_type") == record["item_type"] and old.get("title", "").strip().casefold() == record["title"].strip().casefold() and old.get("statement") != record["statement"]:
                        record["relation_ids"].append(old["id"]); old["relation_ids"] = list(set(old.get("relation_ids", []) + [record["id"]]))
                        if record["item_type"] == "decision": record["status"] = "conflicted"
                objects[record["id"]] = record; result.append(dict(record))
            if cancel_event is not None and cancel_event.is_set(): raise ContextCompileError("cancelled", "Сборка контекста отменена. Данные не изменены.")
            self.store._write_registry(registry)
        return result

    def _receipt_path(self, sanitized_id: str): return self.store.root / "metadata" / f"context_compile_{self.workspace_id}_{sanitized_id}.json"
    def _receipt(self, sanitized_id: str) -> dict[str, Any] | None:
        path=self._receipt_path(sanitized_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    def _restore_receipt(self, receipt: dict[str, Any], sanitized_id: str, compiler_version: str) -> list[dict[str, Any]]:
        if receipt.get("workspace_id") != self.workspace_id or receipt.get("sanitized_id") != sanitized_id or receipt.get("compiler_version") != compiler_version or receipt.get("prompt_schema_version") != PROMPT_SCHEMA_VERSION:
            raise ContextCompileError("receipt_invalid", "Сохранённый результат сборки контекста повреждён или не относится к текущему материалу.")
        context_ids = receipt.get("context_ids")
        if not isinstance(context_ids, list) or not context_ids:
            raise ContextCompileError("receipt_invalid", "Сохранённый результат сборки контекста повреждён или не относится к текущему материалу.")
        result=[]
        for context_id in context_ids:
            if not isinstance(context_id, str): raise ContextCompileError("receipt_invalid", "Сохранённый результат сборки контекста повреждён или не относится к текущему материал.")
            try: record=self.store.object_metadata(self.workspace_id, context_id)
            except ProvenanceError as exc: raise ContextCompileError("receipt_invalid", "Сохранённый результат сборки контекста повреждён или не относится к текущему материалу.") from exc
            if record.get("kind") != "context": raise ContextCompileError("receipt_invalid", "Сохранённый результат сборки контекста повреждён или не относится к текущему материалу.")
            result.append(record)
        return result
    def _write_receipt(self, sanitized_id: str, result: list[dict[str, Any]], chunks: int, compiler_version: str, route: dict[str, Any]) -> None:
        atomic_write_text(self._receipt_path(sanitized_id), json.dumps({"status":"complete","workspace_id":self.workspace_id,"sanitized_id":sanitized_id,"context_ids":[item.get("id","") for item in result],"chunk_count":chunks,"candidate_count":len(result),"compiler_version":compiler_version,"prompt_schema_version":PROMPT_SCHEMA_VERSION,"route":route.get("task", TASK_CONTEXT_COMPILER),"provider":route.get("provider", ""),"model":route.get("model", ""),"completed_at":datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False)+"\n")

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
        if item.get("current") is False:
            raise ProvenanceError("Эта версия предложения устарела. Откройте актуальную версию.")
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
            sections[item["item_type"]].append({key:item.get(key) for key in ("item_type","title","statement","status","actor_ref","deadline","updated_at","source_links","relation_ids")})
        return sections

def _deduplicate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge overlap repeats before persistence, retaining all evidence blocks."""
    result: dict[tuple[str, str], dict[str, Any]] = {}
    confidence = {"low": 0, "medium": 1, "high": 2}
    for candidate in candidates:
        key = (candidate["type"], candidate["statement"].strip().casefold())
        prior = result.get(key)
        if prior is None:
            result[key] = dict(candidate); continue
        if confidence[candidate["confidence"]] > confidence[prior["confidence"]]: prior["confidence"] = candidate["confidence"]
        blocks = prior.get("blocks")
        if blocks is None:
            blocks = [prior.pop("block")]
            prior["blocks"] = blocks
        if candidate["block"] not in blocks: blocks.append(candidate["block"])
    for candidate in result.values():
        blocks = candidate.pop("blocks", None)
        if blocks:
            candidate["block"] = blocks[0]
            candidate["block_links"] = blocks
    return list(result.values())

def validate_candidates(payload: Any, length: int, max_candidates: int = MAX_CANDIDATES) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {"candidates"}:
        raise CandidateValidationError("schema_top_level")
    if not isinstance(payload["candidates"], list) or len(payload["candidates"]) > max_candidates:
        raise CandidateValidationError("schema_candidates")
    if len(json.dumps(payload, ensure_ascii=False)) > MAX_RESULT_SIZE:
        raise CandidateValidationError("result_too_large")
    result=[]
    required={"type","title","statement","block","confidence","requires_review"}
    for item in payload["candidates"]:
        if not isinstance(item,dict): raise CandidateValidationError("schema_candidate")
        if not required.issubset(item): raise CandidateValidationError("schema_required_fields")
        if set(item)-required-OPTIONAL-{RELATIONS_FIELD}: raise CandidateValidationError("schema_unknown_field")
        if item["type"] not in TYPES: raise CandidateValidationError("unknown_type")
        if (not isinstance(item["title"],str) or not 1<=len(item["title"])<=160 or not isinstance(item["statement"],str)
                or not 1<=len(item["statement"])<=1200 or item["confidence"] not in {"low","medium","high"}
                or item["requires_review"] is not True): raise CandidateValidationError("schema_field")
        block=item["block"]
        if not isinstance(block,dict) or set(block)!={"start","end"} or not isinstance(block["start"],int) or not isinstance(block["end"],int) or not 0<=block["start"]<block["end"]<=length: raise CandidateValidationError("block_coordinates")
        for field in OPTIONAL:
            if field in item and not isinstance(item[field],str): raise CandidateValidationError("schema_optional_field")
        if RELATIONS_FIELD in item and (not isinstance(item[RELATIONS_FIELD], list) or len(item[RELATIONS_FIELD]) > 8 or any(not isinstance(value, str) or not value.strip() or len(value) > 160 for value in item[RELATIONS_FIELD])):
            raise CandidateValidationError("schema_relations")
        result.append(dict(item))
    return result
