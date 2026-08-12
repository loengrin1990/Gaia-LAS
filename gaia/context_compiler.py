"""Compile confirmed sanitized material into reviewable provenance context."""
from __future__ import annotations

import json
import re
import time
import uuid
import unicodedata
from datetime import datetime
from typing import Any, Callable

from .provenance import ProvenanceError, ProvenanceStore
from .review import ReviewService
from .local_llm import TASK_CONTEXT_COMPILER, resolve_route, provider_config
from .context_chunking import ChunkLimitError, ContextChunk, split_context
from .storage import atomic_write_text, path_lock
from .runtime_diagnostics import emit as emit_runtime_diagnostic
from .context_model_executor import ContextModelExecutorError, execute_context_model_call

COMPILER_VERSION = "context-v4"
PROMPT_SCHEMA_VERSION = "context-schema-v4-evidence-id"
TYPES = {"requirement", "decision", "risk", "open_question", "action"}
OPTIONAL = {"actor_ref", "deadline", "status", "priority", "reason", "consequence"}
RELATIONS_FIELD = "relations"
MATERIAL_EVIDENCE_IDS_FIELD = "material_evidence_ids"
MATERIAL_EVIDENCE_MODALITIES_FIELD = "material_evidence_modalities"
MATERIAL_EVIDENCE_MODALITIES = {"text", "table_layout", "visual"}
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

def context_response_schema(max_candidates: int, evidence_ids: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    properties = {
        "type": {"type": "string", "enum": sorted(TYPES)},
        "title": {"type": "string", "minLength": 1, "maxLength": 160},
        "statement": {"type": "string", "minLength": 1, "maxLength": 1200},
        "evidence_id": {"type": "string", "enum": list(evidence_ids)},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "requires_review": {"type": "boolean", "const": True},
    }
    for field in OPTIONAL:
        properties[field] = {"type": "string"}
    properties[RELATIONS_FIELD] = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 160}, "maxItems": 8}
    properties[MATERIAL_EVIDENCE_IDS_FIELD] = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 160}, "minItems": 1, "maxItems": 8}
    properties[MATERIAL_EVIDENCE_MODALITIES_FIELD] = {"type": "array", "items": {"type": "string", "enum": sorted(MATERIAL_EVIDENCE_MODALITIES)}, "minItems": 1, "maxItems": 3}
    return {"type": "object", "properties": {"candidates": {"type": "array", "maxItems": max_candidates, "items": {"type": "object", "properties": properties, "required": ["type", "title", "statement", "evidence_id", "confidence", "requires_review"], "additionalProperties": False}}}, "required": ["candidates"], "additionalProperties": False}


def _safe_attempt_category(response: Any, diagnostic_code: str) -> str:
    """Return a fixed, content-free category; never persist model/source values."""
    if diagnostic_code in {"evidence_id_missing", "evidence_id_unknown"}: return diagnostic_code
    if diagnostic_code == "evidence_ambiguous": return "evidence_ambiguous"
    if diagnostic_code != "evidence_mismatch": return "schema"
    candidates = response.get("candidates") if isinstance(response, dict) else None
    if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict): return "schema"
    if "evidence_quote" not in candidates[0]: return "evidence_missing"
    quote = candidates[0].get("evidence_quote")
    if isinstance(quote, str) and not quote: return "evidence_empty"
    return "evidence_not_found"


def local_context_model(text: str, cancel_event: Any = None, section_type_hint: str | None = None, evidence_spans: tuple[Any, ...] = ()) -> dict[str, Any]:
    route = resolve_route(TASK_CONTEXT_COMPILER)
    evidence_ids = [span.id for span in evidence_spans]
    schema = context_response_schema(int(route.get("max_candidates_per_chunk", 16)), evidence_ids) if route.get("structured_output", "schema") == "schema" else None
    choices = "\n".join(f"{span.id}: {span.text}" for span in evidence_spans)
    prompt = (
        "Верни только объект JSON без Markdown. Единственный допустимый ключ верхнего уровня: candidates. "
        "Обязательные поля каждого кандидата: type, title, statement, evidence_id, confidence, requires_review. "
        "title — непустая строка не длиннее 160 символов; statement — непустая строка не длиннее 1200 символов. "
        "evidence_id выбирай только из предложенных точных фрагментов; не возвращай evidence text или координаты. Если подходящего фрагмента нет, верни candidates: []. "
        "type: только requirement, decision, risk, open_question или action. Для русского «Решение» всегда используй type decision; type solution запрещён. confidence: только строка low, medium или high; никогда не число. "
        "requires_review: только JSON boolean true, ключ пишется только requires_review с подчёркиванием. "
        "Не используй русские enum, пробелы в ключах или ключи requirement, solution, risk, question, action как замену структуры кандидата. "
        "Пример: {\"candidates\":[{\"type\":\"requirement\",\"title\":\"Проверка\",\"statement\":\"Проверить материал.\",\"evidence_id\":\"E1\",\"confidence\":\"high\",\"requires_review\":true}]}. "
        + (f"Тип задан структурой документа: {section_type_hint}. Не классифицируй его заново; возвращай только этот type. " if section_type_hint else "Извлеки только явно сказанные требования, решения, риски, вопросы и действия; ")
        + "не добавляй предположений.\n\nФрагменты-основания:\n" + choices + "\n\nМатериал:\n" + text
    )
    try:
        timeout = int(route.get("model_call_timeout_seconds", route.get("timeout_seconds", 120)))
        result = execute_context_model_call({"prompt": prompt, "system": "Ты локальный компилятор проектного контекста Gaia.", "timeout": timeout, "temperature": 0.0, "task": TASK_CONTEXT_COMPILER, "response_schema": schema}, timeout, cancel_event)
    except ContextModelExecutorError as exc:
        if exc.code == "cancelled":
            raise ContextCompileError("cancelled", "Сборка контекста отменена. Данные не изменены.", "cancelled") from None
        code = {"timeout": "model_timeout", "process": "model_process", "result": "model_result"}.get(exc.code, "model_process")
        raise ContextCompileError("local_model_unavailable", "Локальный компилятор контекста недоступен.", code) from None
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
        self._uses_local_provider = model is None and getattr(self.model, "__module__", "") == __name__

    def compile(self, sanitized_id: str, compiler_version: str = COMPILER_VERSION, cancel_event: Any = None, progress: Callable[[int, int, int], None] | None = None, activity: Callable[[int, int, int], None] | None = None, retry_telemetry: Callable[[int, int, int, str | None], None] | None = None) -> list[dict[str, Any]]:
        self._model_attempts = 0  # Legacy counter: semantic units entered, not model calls.
        self._model_call_count = 0
        item = self.preflight(sanitized_id)
        extraction_id = (item.get("parents") or [""])[0]
        extraction = self.store.object_metadata(self.workspace_id, extraction_id)
        self.store.source_metadata(self.workspace_id, (extraction.get("parents") or [""])[0])
        receipt = self._receipt(sanitized_id)
        if receipt and receipt.get("status") == "complete" and receipt.get("compiler_version") == compiler_version:
            return self._restore_receipt(receipt, sanitized_id, compiler_version)
        route = self._route()
        lifecycle_started = False
        terminal_reason = "internal_error"
        try:
            if self._uses_local_provider:
                # From this point on the local model may have been loaded, even when
                # preload itself fails.  The finally block is the single cleanup edge.
                lifecycle_started = True
                if activity: activity(0, 0, 0)
                self._preload(route, cancel_event)
            text = (self.store.root / "sanitized" / self.workspace_id / f"{sanitized_id}.txt").read_text(encoding="utf-8")
            if len(text) > int(route["max_input_chars"]):
                raise ContextCompileError("context_limit", "Материал слишком большой для одной сборки проектного контекста. Разделите его по главам или разделам и повторите обработку. Данные не изменены.")
            try: chunks = split_context(text, int(route["chunk_char_limit"]), int(route["chunk_max_units"]), int(route["chunk_overlap_chars"]), int(route["max_chunks"]))
            except ChunkLimitError as exc: raise ContextCompileError("chunk_limit", "Материал содержит слишком много смысловых единиц для одной сборки контекста. Разделите его по главам или разделам и повторите обработку.") from exc
            candidates=[]
            for position, chunk in enumerate(chunks, 1):
                if cancel_event is not None and cancel_event.is_set(): raise ContextCompileError("cancelled", "Сборка контекста отменена. Данные не изменены.")
                if activity: activity(position, len(chunks), self._model_attempts + 1)
                local = self._compile_chunk(chunk, route, cancel_event, retry_telemetry)
                candidates.extend(local)
                if len(candidates) > int(route["max_total_candidates"]): raise ContextCompileError("total_candidate_limit", "Слишком много элементов контекста. Данные не изменены.")
                if progress: progress(position, len(chunks), len(candidates))
            if activity: activity(-1, len(chunks), self._model_attempts)
            candidates = _deduplicate_candidates(candidates)
            if cancel_event is not None and cancel_event.is_set(): raise ContextCompileError("cancelled", "Сборка контекста отменена. Данные не изменены.")
            if activity: activity(-2, len(chunks), self._model_attempts)
            result = self._persist_all(item, sanitized_id, candidates, compiler_version, route, cancel_event)
            if activity: activity(-3, len(chunks), self._model_attempts)
            self._write_receipt(sanitized_id, result, len(chunks), compiler_version, route)
            terminal_reason = "success"
            return result
        except ContextCompileError as exc:
            terminal_reason = exc.diagnostic_code or exc.code
            raise
        except Exception:
            terminal_reason = "internal_error"
            raise
        finally:
            if lifecycle_started:
                self._unload(route, terminal_reason)

    def preflight(self, sanitized_id: str) -> dict[str, Any]:
        item = self.store.object_metadata(self.workspace_id, sanitized_id)
        if item.get("kind") != "sanitized": raise ContextCompileError("material_unavailable", "Очищенный материал недоступен в выбранном рабочем пространстве.")
        if not item.get("current"): raise ContextCompileError("stale_version", "Эта версия больше не актуальна.")
        review = ReviewService(self.store, self.workspace_id).get(sanitized_id)
        if not review.get("confirmed"): raise ContextCompileError("material_not_confirmed", "Сначала подтвердите очищенный материал.")
        return item

    def _route(self) -> dict[str, Any]:
        route = resolve_route(TASK_CONTEXT_COMPILER)
        defaults = {"prompt_char_limit": 9000, "max_tokens": 2400, "context_length": 32768, "timeout_seconds": 120, "model_load_timeout_seconds": 300, "model_call_timeout_seconds": 240, "model_keep_alive": "30m", "unload_model_after_job": True, "chunk_char_limit": 4000, "chunk_max_units": 12, "chunk_overlap_chars": 250, "max_candidates_per_chunk": 16, "max_total_candidates": 512, "max_input_chars": MAX_INPUT_SIZE, "max_chunks": 80, "retry_count": 1, "job_timeout_seconds": 1800, "max_model_attempts": 256}
        return {**defaults, **route}

    def _preload(self, route: dict[str, Any], cancel_event: Any) -> None:
        provider = provider_config(str(route["provider"]))
        if provider.get("type") != "ollama": return
        timeout = int(route.get("model_load_timeout_seconds", route.get("timeout_seconds", 120)))
        try:
            result = execute_context_model_call({"operation": "preload", "endpoint": str(provider.get("endpoint")), "model": str(route["model"]), "keep_alive": str(route.get("model_keep_alive", "30m")), "timeout": timeout}, timeout, cancel_event)
        except ContextModelExecutorError as exc:
            if exc.code == "cancelled": raise ContextCompileError("cancelled", "Сборка контекста отменена. Данные не изменены.", "cancelled") from None
            code = "model_load_timeout" if exc.code == "timeout" else "model_process"
            raise ContextCompileError("local_model_unavailable", "Локальная модель не успела загрузиться. Данные не изменены.", code) from None
        if not result.get("ok"): raise ContextCompileError("local_model_unavailable", "Локальная модель не успела загрузиться. Данные не изменены.", "model_load_timeout")

    def _unload(self, route: dict[str, Any], terminal_reason: str) -> None:
        if not route.get("unload_model_after_job"):
            emit_runtime_diagnostic("context_compile_model", "context_compile_model_unload", f"gaia-{uuid.uuid4().hex[:12]}", route=TASK_CONTEXT_COMPILER, attempted=False, succeeded=False, failed=False, elapsed_ms=0, terminal_reason=terminal_reason)
            return
        provider = provider_config(str(route["provider"]))
        if provider.get("type") != "ollama":
            emit_runtime_diagnostic("context_compile_model", "context_compile_model_unload", f"gaia-{uuid.uuid4().hex[:12]}", route=TASK_CONTEXT_COMPILER, attempted=False, succeeded=False, failed=False, elapsed_ms=0, terminal_reason=terminal_reason)
            return
        started = time.monotonic()
        try:
            execute_context_model_call({"operation": "unload", "endpoint": str(provider.get("endpoint")), "model": str(route["model"]), "timeout": 15}, 15)
        except Exception:
            # Cleanup is deliberately best effort: it cannot overwrite the actual
            # compiler outcome, including a successful persisted receipt.
            emit_runtime_diagnostic("context_compile_model", "context_compile_model_unload", f"gaia-{uuid.uuid4().hex[:12]}", route=TASK_CONTEXT_COMPILER, attempted=True, failed=True, succeeded=False, elapsed_ms=int((time.monotonic()-started)*1000), terminal_reason=terminal_reason)
        else:
            emit_runtime_diagnostic("context_compile_model", "context_compile_model_unload", f"gaia-{uuid.uuid4().hex[:12]}", route=TASK_CONTEXT_COMPILER, attempted=True, failed=False, succeeded=True, elapsed_ms=int((time.monotonic()-started)*1000), terminal_reason=terminal_reason)

    def _compile_chunk(self, unit: ContextChunk, route: dict[str, Any], cancel_event: Any, retry_telemetry: Callable[[int, int, int, str | None], None] | None = None) -> list[dict[str, Any]]:
        failures: list[ContextCompileError] = []
        self._model_attempts += 1
        if self._model_attempts > int(route["max_model_attempts"]):
            raise ContextCompileError("model_attempt_limit", "Превышен безопасный предел попыток сборки контекста. Данные не изменены.")
        for call_number in range(1, int(route["retry_count"]) + 2):
            if cancel_event is not None and cancel_event.is_set(): raise ContextCompileError("cancelled", "Сборка контекста отменена. Данные не изменены.")
            self._model_call_count += 1
            if retry_telemetry: retry_telemetry(unit.index + 1, call_number, self._model_call_count, None)
            response = None
            try:
                production_model = self.model is local_context_model
                if production_model:
                    response = self.model(unit.text, cancel_event=cancel_event, section_type_hint=unit.section_type_hint, evidence_spans=unit.evidence_spans)
                else:
                    try:
                        response = self.model(unit.text, cancel_event=cancel_event)
                    except TypeError:
                        response = self.model(unit.text)
                local = validate_candidates(response, len(unit.text), int(route["max_candidates_per_chunk"] if production_model else MAX_CANDIDATES), allow_legacy=not production_model, evidence_ids={span.id for span in unit.evidence_spans} if production_model else None)
                local = _ground_candidates(local, unit)
                if len(local) > int(route["max_candidates_per_chunk"]):
                    raise CandidateValidationError("schema_candidates")
                if retry_telemetry: retry_telemetry(unit.index + 1, call_number, self._model_call_count, "exact")
                return local
            except CandidateValidationError as exc:
                code = exc.diagnostic_code
                category = _safe_attempt_category(response if "response" in locals() else None, code)
                if retry_telemetry: retry_telemetry(unit.index + 1, call_number, self._model_call_count, category)
                evidence_error = code in {"evidence_mismatch", "evidence_ambiguous"}
                failures.append(ContextCompileError(
                    "context_evidence_ambiguous" if code == "evidence_ambiguous" else "context_evidence_mismatch" if code == "evidence_mismatch" else "local_model_invalid",
                    "Не удалось однозначно подтвердить фрагмент-основание. Данные не изменены." if code == "evidence_ambiguous" else "Не удалось точно подтвердить фрагмент-основание. Данные не изменены." if evidence_error else "Локальный компилятор вернул результат, который не прошёл проверку.",
                    code,
                ))
            except ContextCompileError as exc:
                category = "provider" if exc.diagnostic_code in {"provider_unavailable", "model_timeout", "model_process", "model_result"} else "schema"
                if retry_telemetry: retry_telemetry(unit.index + 1, call_number, self._model_call_count, category)
                if exc.diagnostic_code in {"empty_response", "output_truncated", "json_parse", "schema_top_level", "schema_candidates", "schema_required_fields", "schema_unknown_field", "block_coordinates", "result_too_large"}:
                    failures.append(exc)
                else: raise
            except Exception:
                if retry_telemetry: retry_telemetry(unit.index + 1, call_number, self._model_call_count, "other_safe")
                failures.append(ContextCompileError("local_model_unavailable", "Локальный компилятор контекста недоступен.", "provider_unavailable"))
        raise failures[-1]


    def _persist_all(self, item: dict[str, Any], sanitized_id: str, candidates: list[dict[str, Any]], compiler_version: str, route: dict[str, Any], cancel_event: Any) -> list[dict[str, Any]]:
        if cancel_event is not None and cancel_event.is_set(): raise ContextCompileError("cancelled", "Сборка контекста отменена. Данные не изменены.")
        with path_lock(self.store.registry_path):
            registry = self.store._registry(); objects = registry["objects"]; result: list[dict[str, Any]] = []
            for candidate in candidates:
                material_evidence = self._resolve_material_evidence(sanitized_id, candidate)
                duplicate = next((x for x in objects.values() if x.get("kind") == "context" and x.get("workspace_id") == self.workspace_id and x.get("item_type") == candidate["type"] and str(x.get("statement", "")).strip().casefold() == candidate["statement"].strip().casefold()), None)
                if duplicate:
                    duplicate = dict(duplicate); sources = list(duplicate.get("source_links") or [])
                    if sanitized_id not in sources: sources.append(sanitized_id)
                    duplicate["source_links"] = sources; objects[duplicate["id"]] = duplicate; result.append(dict(duplicate)); continue
                values = {key: candidate.get(key) for key in OPTIONAL - {"status"} if key in candidate}
                if "status" in candidate: values["explicit_status"] = candidate["status"]
                if RELATIONS_FIELD in candidate: values["proposed_relations"] = candidate[RELATIONS_FIELD]
                blocks = list(candidate.get("block_links", [candidate["block"]]))
                for evidence in material_evidence:
                    if evidence["block"] not in blocks:
                        blocks.append(evidence["block"])
                record = self.store._record(self.store._id("ctx"), self.workspace_id, "context", item_type=candidate["type"], parents=[sanitized_id], source_links=[sanitized_id], block_links=blocks, material_evidence_ids=list(candidate.get(MATERIAL_EVIDENCE_IDS_FIELD, [])), material_evidence_modalities=list(candidate.get(MATERIAL_EVIDENCE_MODALITIES_FIELD, [])), material_evidence_links=[{key: evidence[key] for key in ("evidence_id", "modality", "page", "locator", "origin")} for evidence in material_evidence], title=candidate["title"], statement=candidate["statement"], status="requires_review", confidence=candidate["confidence"], requires_review=True, compiler_version=compiler_version, prompt_schema_version=PROMPT_SCHEMA_VERSION, model_route=str(route["provider"]), model_name=str(route["model"]), version=1, supersedes_id="", confirmation_status="pending", relation_ids=[], current=True, export_allowed=False, **values)
                for old in objects.values():
                    if old.get("workspace_id") != self.workspace_id or old.get("kind") != "context": continue
                    if old.get("item_type") == record["item_type"] and old.get("title", "").strip().casefold() == record["title"].strip().casefold() and old.get("statement") != record["statement"]:
                        record["relation_ids"].append(old["id"]); old["relation_ids"] = list(set(old.get("relation_ids", []) + [record["id"]]))
                        if record["item_type"] == "decision": record["status"] = "conflicted"
                objects[record["id"]] = record; result.append(dict(record))
            if cancel_event is not None and cancel_event.is_set(): raise ContextCompileError("cancelled", "Сборка контекста отменена. Данные не изменены.")
            self.store._write_registry(registry)
        return result

    def _resolve_material_evidence(self, sanitized_id: str, candidate: dict[str, Any]) -> list[dict[str, Any]]:
        """Resolve declared material evidence before any context record is written."""
        evidence_ids = candidate.get(MATERIAL_EVIDENCE_IDS_FIELD)
        if not evidence_ids:
            return []
        sanitized = self.store.object_metadata(self.workspace_id, sanitized_id)
        extraction_id = (sanitized.get("parents") or [""])[0]
        path = self.store.root / "artifacts" / self.workspace_id / f"{extraction_id}.evidence.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContextCompileError("context_material_evidence_unavailable", "Не удалось подтвердить фрагменты материала для кандидата.", "material_evidence_unavailable") from exc
        if manifest.get("extraction_id") != extraction_id:
            raise ContextCompileError("context_material_evidence_unavailable", "Не удалось подтвердить фрагменты материала для кандидата.", "material_evidence_unavailable")
        known = {item.get("evidence_id"): item for item in manifest.get("evidence", [])}
        body = (self.store.root / "sanitized" / self.workspace_id / f"{sanitized_id}.txt").read_text(encoding="utf-8")
        resolved: list[dict[str, Any]] = []
        for evidence_id in evidence_ids:
            item = known.get(evidence_id)
            if not item:
                raise ContextCompileError("context_material_evidence_invalid", "Указанный фрагмент материала недоступен для кандидата.", "material_evidence_unknown")
            if item.get("state") != "ready" or item.get("modality") not in MATERIAL_EVIDENCE_MODALITIES:
                raise ContextCompileError("context_material_evidence_unavailable", "Указанный фрагмент материала не может подтвердить кандидата.", "material_evidence_unsupported")
            fragment = _material_evidence_fragment(str(item.get("text") or ""))
            start = body.find(fragment) if fragment else -1
            if start < 0:
                raise ContextCompileError("context_material_evidence_unavailable", "Указанный фрагмент материала не доступен в очищенном представлении.", "material_evidence_unavailable")
            resolved.append({
                "evidence_id": evidence_id, "modality": item["modality"], "page": item["page"],
                "locator": item["locator"], "origin": item["origin"],
                "block": {"start": start, "end": start + len(fragment)},
            })
        required = set(candidate.get(MATERIAL_EVIDENCE_MODALITIES_FIELD, []))
        if required and not required.issubset({item["modality"] for item in resolved}):
            raise ContextCompileError("context_material_evidence_invalid", "Указанные фрагменты не покрывают обязательные способы извлечения.", "material_evidence_modality")
        return resolved

    def _receipt_path(self, sanitized_id: str): return self.store.root / "metadata" / f"context_compile_{self.workspace_id}_{sanitized_id}.json"
    def _receipt(self, sanitized_id: str) -> dict[str, Any] | None:
        path=self._receipt_path(sanitized_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    def _restore_receipt(self, receipt: dict[str, Any], sanitized_id: str, compiler_version: str) -> list[dict[str, Any]]:
        if receipt.get("workspace_id") != self.workspace_id or receipt.get("sanitized_id") != sanitized_id or receipt.get("compiler_version") != compiler_version or receipt.get("prompt_schema_version") != PROMPT_SCHEMA_VERSION:
            raise ContextCompileError("receipt_invalid", "Сохранённый результат сборки контекста повреждён или не относится к текущему материалу.")
        context_ids = receipt.get("context_ids")
        if not isinstance(context_ids, list):
            raise ContextCompileError("receipt_invalid", "Сохранённый результат сборки контекста повреждён или не относится к текущему материалу.")
        if not context_ids:
            return []
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
        if decision == "confirm": self.store._update(context_id, status="confirmed", confirmation_status="confirmed", requires_review=False, confirmed_at=datetime.now().isoformat(timespec="seconds")); return self.get(context_id)
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

def validate_candidates(payload: Any, length: int, max_candidates: int = MAX_CANDIDATES, *, allow_legacy: bool = False, evidence_ids: set[str] | None = None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {"candidates"}:
        raise CandidateValidationError("schema_top_level")
    if not isinstance(payload["candidates"], list) or len(payload["candidates"]) > max_candidates:
        raise CandidateValidationError("schema_candidates")
    if len(json.dumps(payload, ensure_ascii=False)) > MAX_RESULT_SIZE:
        raise CandidateValidationError("result_too_large")
    result=[]
    required = {"type", "title", "statement", "evidence_id", "confidence", "requires_review"}
    for item in payload["candidates"]:
        if not isinstance(item, dict):
            raise CandidateValidationError("schema_candidate")
        legacy_block = allow_legacy and "evidence_id" not in item and "block" in item
        legacy_quote = allow_legacy and "evidence_id" not in item and "evidence_quote" in item
        if legacy_block:
            active_required = {"type", "title", "statement", "block", "confidence", "requires_review"}
        elif legacy_quote:
            active_required = {"type", "title", "statement", "evidence_quote", "confidence", "requires_review"}
        else:
            active_required = required
            if "evidence_id" not in item:
                raise CandidateValidationError("evidence_id_missing")
        if not active_required.issubset(item):
            raise CandidateValidationError("schema_required_fields")
        if set(item) - active_required - OPTIONAL - {RELATIONS_FIELD, MATERIAL_EVIDENCE_IDS_FIELD, MATERIAL_EVIDENCE_MODALITIES_FIELD}:
            raise CandidateValidationError("schema_unknown_field")
        if item["type"] not in TYPES: raise CandidateValidationError("unknown_type")
        if (not isinstance(item["title"],str) or not 1<=len(item["title"])<=160 or not isinstance(item["statement"],str)
                or not 1<=len(item["statement"])<=1200 or item["confidence"] not in {"low","medium","high"}
                or item["requires_review"] is not True): raise CandidateValidationError("schema_field")
        if legacy_block:
            block=item["block"]
            if not isinstance(block,dict) or set(block)!={"start","end"} or not isinstance(block["start"],int) or not isinstance(block["end"],int) or not 0<=block["start"]<block["end"]<=length: raise CandidateValidationError("block_coordinates")
        elif legacy_quote:
            quote = item["evidence_quote"]
            if not isinstance(quote, str) or not quote:
                raise CandidateValidationError("evidence_mismatch")
        elif "evidence_id" not in item:
            raise CandidateValidationError("evidence_id_missing")
        elif not isinstance(item["evidence_id"], str) or evidence_ids is None or item["evidence_id"] not in evidence_ids:
            raise CandidateValidationError("evidence_id_unknown")
        for field in OPTIONAL:
            if field in item and not isinstance(item[field],str): raise CandidateValidationError("schema_optional_field")
        if RELATIONS_FIELD in item and (not isinstance(item[RELATIONS_FIELD], list) or len(item[RELATIONS_FIELD]) > 8 or any(not isinstance(value, str) or not value.strip() or len(value) > 160 for value in item[RELATIONS_FIELD])):
            raise CandidateValidationError("schema_relations")
        if MATERIAL_EVIDENCE_IDS_FIELD in item and (not isinstance(item[MATERIAL_EVIDENCE_IDS_FIELD], list) or not item[MATERIAL_EVIDENCE_IDS_FIELD] or len(item[MATERIAL_EVIDENCE_IDS_FIELD]) > 8 or len(set(item[MATERIAL_EVIDENCE_IDS_FIELD])) != len(item[MATERIAL_EVIDENCE_IDS_FIELD]) or any(not isinstance(value, str) or not value.strip() or len(value) > 160 for value in item[MATERIAL_EVIDENCE_IDS_FIELD])):
            raise CandidateValidationError("schema_material_evidence")
        if MATERIAL_EVIDENCE_MODALITIES_FIELD in item and (MATERIAL_EVIDENCE_IDS_FIELD not in item or not isinstance(item[MATERIAL_EVIDENCE_MODALITIES_FIELD], list) or not item[MATERIAL_EVIDENCE_MODALITIES_FIELD] or len(item[MATERIAL_EVIDENCE_MODALITIES_FIELD]) > 3 or len(set(item[MATERIAL_EVIDENCE_MODALITIES_FIELD])) != len(item[MATERIAL_EVIDENCE_MODALITIES_FIELD]) or any(value not in MATERIAL_EVIDENCE_MODALITIES for value in item[MATERIAL_EVIDENCE_MODALITIES_FIELD])):
            raise CandidateValidationError("schema_material_evidence_modalities")
        result.append(dict(item))
    return result


def _metadata_normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def _material_evidence_fragment(value: str) -> str:
    """Match the exact page-level representation sent through the text path."""
    return " ".join(line.strip() for line in value.splitlines() if line.strip())


def _validate_optional_metadata_source(candidates: list[dict[str, Any]], text: str) -> None:
    """Keep visible actor references tied to the cleaned fragment, never a model invention."""
    fragment = _metadata_normalize(text)
    for candidate in candidates:
        value = candidate.get("actor_ref")
        normalized = _metadata_normalize(value)
        if value and (not normalized or normalized not in fragment):
            raise CandidateValidationError("metadata_not_in_fragment")


def _ground_candidates(candidates: list[dict[str, Any]], unit: ContextChunk) -> list[dict[str, Any]]:
    """Map exact evidence to source offsets and derive optional metadata locally."""
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate = dict(candidate)
        if "evidence_id" in candidate:
            evidence_id = candidate.pop("evidence_id")
            span = next((value for value in unit.evidence_spans if value.id == evidence_id), None)
            if span is None: raise CandidateValidationError("evidence_id_unknown")
            candidate["block"] = {"start": span.global_start, "end": span.global_end}
            candidate["type"] = unit.section_type_hint or candidate["type"]
            # These fields have no deterministic grounding rule in the Stage 7
            # evidence contract.  Historical records and injected legacy test
            # doubles remain readable; new evidence-based extraction never stores
            # a model-supplied causal statement or proposed relation.
            candidate.pop("reason", None)
            candidate.pop("consequence", None)
            candidate.pop(RELATIONS_FIELD, None)
            candidate.update(_ground_metadata(span.text))
        elif "evidence_quote" in candidate:  # Legacy deterministic test doubles only.
            quote = candidate.pop("evidence_quote")
            matches = [match.start() for match in re.finditer(re.escape(quote), unit.text)]
            if not matches: raise CandidateValidationError("evidence_mismatch")
            if len(matches) > 1: raise CandidateValidationError("evidence_ambiguous")
            candidate["block"] = {"start": unit.start + matches[0], "end": unit.start + matches[0] + len(quote)}
            candidate["type"] = unit.section_type_hint or candidate["type"]
            candidate.pop("reason", None); candidate.pop("consequence", None); candidate.pop(RELATIONS_FIELD, None)
            candidate.update(_ground_metadata(quote))
        else:
            # Compatibility only for injected deterministic test doubles; production
            # model calls always pass through the exact-evidence branch above.
            candidate["block"] = {"start": unit.start + candidate["block"]["start"], "end": unit.start + candidate["block"]["end"]}
            _validate_optional_metadata_source([candidate], unit.text)
        result.append(candidate)
    return result


_CALENDAR_DEADLINE_RE = re.compile(
    r"\bдо\s+\d{1,2}\s+[а-яё]+\s+\d{4}\s+года\b"
    r"|\bназначен\w*\s+на\s+\d{1,2}\s+[а-яё]+\s+\d{4}\s+года\b",
    re.I,
)
_HUMAN_DEADLINE_RE = re.compile(
    r"\b(?:до\s+конца|к\s+концу)\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря|недели|месяца|квартала|года)\b"
    r"|\bк\s+следующей\s+встрече\b"
    r"|\bв\s+течение\s+(?:\d+|[а-яё]+)\s+(?:рабоч\w*\s+)?(?:дн\w*|день)\b"
    r"|\bза\s+(?:\d+|[а-яё]+)\s+(?:рабоч\w*\s+)?(?:дн\w*|день)\s+до\s+[^\n.,;:!?]{1,120}(?=[\n.,;:!?]|$)"
    r"|\bдо\s+старта\s+[^\n.,;:!?]{1,120}(?=[\n.,;:!?]|$)",
    re.I,
)


def _ground_metadata(evidence: str) -> dict[str, Any]:
    actor = re.search(r"\[[^\]\n]{1,120}\]", evidence)
    # A bracketed pseudonym is accepted only when an explicit responsibility or
    # appointment construction is present; unresolved responsibility is not one.
    unresolved = re.search(r"ответственн\w*(?:\s+\[[^\]\n]+\])?\s+(?:пока\s+)?не\s+(?:назначен|определ[её]н)", evidence, re.I)
    actor_value = actor.group(0) if actor and not unresolved and re.search(r"(?:ответственн\w*|владельц\w*|назнач\w*|долж\w*)", evidence, re.I) else ""
    deadline = _CALENDAR_DEADLINE_RE.search(evidence) or _HUMAN_DEADLINE_RE.search(evidence)
    status = re.search(r"\bСтатус:\s*([^\n.;]+)", evidence, re.I)
    priority = re.search(r"\bПриоритет:\s*([^\n.;]+)", evidence, re.I)
    negated_deadline = deadline and re.search(r"\bне\s+назначен\w*\s+на\s+\d{1,2}\s+[а-яё]+\s+\d{4}\s+года\b", evidence, re.I)
    return {"actor_ref": actor_value or None, "deadline": deadline.group(0) if deadline and not negated_deadline else None, "status": status.group(1).strip() if status else None, "priority": priority.group(1).strip() if priority else None}
