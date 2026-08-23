"""Isolated D3.1B local-reasoning experiment over an unchanged D3.1A package."""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .local_llm import local_llm_payload, provider_config, response_content, route_configs

LOCAL_REASONING = "local_reasoning"
EXPERIMENTAL_DEFAULT_PROVIDER = "ollama_ministral_3_14b"
SYSTEM_INSTRUCTION = """Отвечай только по supplied reasoning context. Source evidence и OC overlay — разные слои; OC не исчерпывает знание проекта. Не переноси правило между разными явно указанными subject scope. Явные current/final cues источника учитывай выше stale/pending формулировок OC только если это следует из самого evidence; не выводи currentness из timestamp. Не усиливай утверждения сверх evidence: сохраняй scope, qualifiers, quantifiers, exclusivity, modality и confidence; не выводи только/все/обязательно/точно/единственный/единственная/only/sole без явной опоры в evidence. Статус «авторитетный/authoritative» сам по себе не означает «единственный/only/sole/source of truth»; эксклюзивность допустима только если она явно утверждается evidence. При неразрешимом расхождении скажи об неопределённости. Дай краткий свободный текстовый ответ. Не включай внутренние идентификаторы evidence (например, src_, san_ или OC ID) в основной текст ответа; они допустимы только в служебной строке USED_FACTS. Последней отдельной строкой обязательно укажи только использованные identifiers evidence в виде: USED_FACTS: <id>, <id>."""

@dataclass(frozen=True)
class ReasoningRoute:
    capability: str; provider: str; model: str; temperature: float = 0; max_tokens: int = 800; context_length: int = 16384; timeout_seconds: int = 120

def resolve_local_reasoning() -> ReasoningRoute:
    configured = route_configs().get(LOCAL_REASONING)
    if not isinstance(configured, dict):
        raise RuntimeError("local_reasoning route is not configured.")
    provider_name = str(configured.get("provider") or "")
    if not provider_name:
        raise RuntimeError("local_reasoning route provider is not configured.")
    provider = provider_config(provider_name)
    if provider.get("type") != "ollama" or provider.get("enabled", True) is not True:
        raise RuntimeError("local_reasoning provider is unavailable.")
    model = str(configured.get("model") or provider.get("model") or "")
    if not model:
        raise RuntimeError("local_reasoning route model is not configured.")
    return ReasoningRoute(LOCAL_REASONING, provider_name, model, float(configured.get("temperature", 0)), int(configured.get("max_tokens", 800)), int(configured.get("context_length", provider.get("context_length", 16384))), int(configured.get("timeout_seconds", 120)))

def serialize_reasoning_context(package: dict[str, Any]) -> str:
    """Pure compact view; the original package stays the experiment audit record."""
    context = {"schema":"gaia.d3.reasoning-context.v1","query":package["query"],
      "source_evidence":[{"excerpt":x["excerpt"],"source_id":x["source_id"],"sanitized_id":x["sanitized_id"],"start_offset":x["start_offset"],"end_offset":x["end_offset"],"literal_annotations":x.get("literal_annotations",{}),"selection_role":x.get("selection_role","primary_hit"),"expansion_relation":x.get("expansion_relation","")} for x in package.get("source_evidence",[])],
      "oc_overlay":[{"id":x["item"].get("id",""),"value":x["item"].get("value",x["item"].get("reference","")),"lifecycle":x["item"].get("lifecycle",""),"confirmation":x["item"].get("confirmation",""),"provenance":x["item"].get("provenance",{}),"relevance_score":x.get("relevance_score")} for x in package.get("oc_overlay",[])],
      "observed_tensions":package.get("observed_tensions",[]),"unresolved":package.get("unresolved",[]),
      "meta":{"source_hit_count":len(package.get("source_evidence",[])),"oc_hit_count":len(package.get("oc_overlay",[]))}}
    return json.dumps(context,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def _ollama_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """Preserve response metadata without duplicating the captured content."""
    return {key: value for key, value in data.items() if key != "message"}


def _answer_and_used_facts(raw: str) -> tuple[str, list[str]]:
    match = re.search(r"(?:^|\n)USED_FACTS:\s*(.+?)\s*$", raw, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return raw.strip(), []
    answer = raw[:match.start()].strip()
    facts = [value.strip() for value in match.group(1).split(",") if value.strip()]
    return answer, facts


def run_hybrid_reasoning(package: dict[str, Any], *, request: Callable[..., Any] = urllib.request.urlopen) -> dict[str, Any]:
    route = resolve_local_reasoning(); prompt = serialize_reasoning_context(package)
    provider = provider_config(route.provider)
    payload = local_llm_payload(provider, route.model, SYSTEM_INSTRUCTION, prompt, route.temperature, max_tokens=route.max_tokens, context_length=route.context_length, json_mode=False)
    started = time.monotonic()
    try:
        req = urllib.request.Request(str(provider["endpoint"]), data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with request(req, timeout=route.timeout_seconds) as response: data = json.loads(response.read().decode())
        raw = response_content(provider, data)
        raw_content_sha256 = hashlib.sha256(raw.encode()).hexdigest()
        metadata = _ollama_metadata(data)
        result_base = {"capability": route.capability, "provider": route.provider, "model": route.model, "package_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "instruction_sha256": hashlib.sha256(SYSTEM_INSTRUCTION.encode()).hexdigest(), "model_options": {"temperature": route.temperature, "num_ctx": route.context_length, "num_predict": route.max_tokens}, "raw_content": raw, "raw_content_sha256": raw_content_sha256, "ollama_metadata": metadata, "done_reason": str(data.get("done_reason") or ""), "prompt_eval_count": data.get("prompt_eval_count"), "eval_count": data.get("eval_count"), "total_duration": data.get("total_duration"), "load_duration": data.get("load_duration"), "prompt_eval_duration": data.get("prompt_eval_duration"), "eval_duration": data.get("eval_duration")}
        if data.get("done") is not True:
            return {"ok": False, "status": "PROVIDER_INCOMPLETE_RESPONSE", "error": "PROVIDER_INCOMPLETE_RESPONSE", **result_base, "elapsed_seconds": round(time.monotonic()-started, 3)}
        answer, facts = _answer_and_used_facts(raw)
        if not answer or not facts or data.get("done_reason") == "length": raise ValueError("incomplete reasoning response")
        return {"ok": True, "status": "OK", **result_base, "answer": answer, "used_facts": facts, "elapsed_seconds": round(time.monotonic()-started, 3)}
    except Exception as exc:
        return {"ok": False, "capability": route.capability, "provider": route.provider, "model": route.model, "package_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "error": "local_reasoning_failed", "detail": str(exc), "elapsed_seconds": round(time.monotonic()-started, 3)}
