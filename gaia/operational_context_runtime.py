"""OC-5 runtime boundary for a prepared Operational Context package.

The boundary deliberately owns neither retrieval nor composition.  It accepts
only the typed OC-3 package, checks the normal single-pass envelope before a
provider call, and lets PB-0 decide whether an external seam is available.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .local_llm import provider_config, resolve_route, run_local_llm_prompt
from .operational_context_assembler import OperationalContextPackage


OC5_TASK = "operational_context_dialogue"
OC5_LOCAL_MODEL = "qwen3.5:9b"
NORMAL_CONTEXT_TOKENS = 16_384
OC5_RESPONSE_TOKENS = 900
OC5_SYSTEM_MESSAGE = (
    "Отвечай только по подготовленному контексту. Поле current_authority содержит "
    "подтверждённые актуальные факты и имеет приоритет для вопроса пользователя. "
    "Если в current_authority есть subject_ref delivery_status, используй его value для "
    "вопроса о статусе поставки. Не утверждай, что подтверждённого контекста нет, если "
    "current_authority не пуст. Если в ambiguities есть alternatives, прямо скажи, что "
    "это противоречие, и перечисли их content, но не выбирай сторону. "
    "Не выводи внутренние поля response_text, reasoning или служебные схемы."
)
OC5_RECOVERY_SYSTEM_MESSAGE = (
    "Сформируй только естественный русский ответ по тому же подготовленному контексту. "
    "Не выводи JSON, поля схемы, reasoning или служебную ошибку. При ambiguity укажи обе "
    "альтернативы и не выбирай победителя."
)
SAFE_OVERSIZE_MESSAGE = (
    "Для надёжного ответа контекст слишком велик для одного локального прохода. "
    "Сузьте вопрос или обработайте материал по этапам."
)
SAFE_PROVIDER_MESSAGE = "Локальная модель сейчас недоступна. Попробуйте ещё раз позже."
SAFE_MISSING_CONTEXT_MESSAGE = "В подтверждённом текущем контексте проекта статус поставки не найден."
SAFE_RUNTIME_RESPONSE_MESSAGE = "Не удалось сформировать ответ по подтверждённому текущему контексту. Попробуйте ещё раз позже."


@dataclass(frozen=True)
class OperationalContextRuntimeResult:
    ok: bool
    route: str
    model: str
    answer: str
    package_tokens: int
    oversize_rejected: bool = False


def estimate_tokens(text: str) -> int:
    """Strict byte upper bound: a tokenizer cannot use more tokens than bytes."""
    return len(text.encode("utf-8"))


def render_runtime_prompt(package: OperationalContextPackage) -> str:
    """Render the complete prepared package without selecting a conflict winner."""
    payload = package.as_dict()
    return json.dumps(payload, ensure_ascii=False, default=_json_default, sort_keys=True)


def run_operational_context_dialogue(
    package: OperationalContextPackage,
    *,
    local_executor: Callable[..., dict[str, Any]] = run_local_llm_prompt,
    external_executor: Callable[[str], dict[str, Any]] | None = None,
) -> OperationalContextRuntimeResult:
    if not isinstance(package, OperationalContextPackage):
        raise TypeError("OC-5 requires a typed Operational Context package.")
    prompt = render_runtime_prompt(package)
    prompt_tokens = estimate_tokens(prompt)
    route = "external_eligible" if package.metadata.disclosure.eligible_for_external else "local"
    if package.omissions:
        return OperationalContextRuntimeResult(False, route, OC5_LOCAL_MODEL, SAFE_OVERSIZE_MESSAGE, prompt_tokens, True)
    # UTF-8 byte count is a conservative upper bound: it may reject a package
    # that a tokenizer would accept, but it cannot hand Ollama an unchecked
    # oversize authority package.  Reserve the configured response allowance.
    system_message = _runtime_system_message(package)
    request_tokens = prompt_tokens + estimate_tokens(system_message)
    if request_tokens + OC5_RESPONSE_TOKENS > NORMAL_CONTEXT_TOKENS:
        return OperationalContextRuntimeResult(False, route, OC5_LOCAL_MODEL, SAFE_OVERSIZE_MESSAGE, prompt_tokens, True)

    if route == "external_eligible" and external_executor is not None:
        response = external_executor(prompt)
        return OperationalContextRuntimeResult(
            bool(response.get("ok")), route, str(response.get("model") or "external"),
            _user_facing_answer(str(response.get("answer") or SAFE_PROVIDER_MESSAGE), package), prompt_tokens,
        )
    if route == "external_eligible":
        return OperationalContextRuntimeResult(
            False, route, "", "Внешняя обработка для этого сценария пока не подключена.", prompt_tokens,
        )

    local_route = resolve_route(OC5_TASK)
    provider = provider_config(str(local_route["provider"]))
    if (
        local_route.get("model") != OC5_LOCAL_MODEL
        or provider.get("model") != OC5_LOCAL_MODEL
        or provider.get("type") != "ollama"
        or local_route.get("context_length") != NORMAL_CONTEXT_TOKENS
        or local_route.get("max_tokens") != OC5_RESPONSE_TOKENS
        or int(local_route.get("prompt_char_limit") or 0) < len(prompt)
    ):
        return OperationalContextRuntimeResult(False, "local", OC5_LOCAL_MODEL, SAFE_PROVIDER_MESSAGE, prompt_tokens)
    response = local_executor(
        prompt,
        system_message,
        task=OC5_TASK,
        timeout=180,
        json_mode=False,
    )
    if not response.get("ok"):
        return OperationalContextRuntimeResult(False, "local", OC5_LOCAL_MODEL, SAFE_PROVIDER_MESSAGE, prompt_tokens)
    raw_answer = str(response.get("answer") or "")
    if _requires_recovery(raw_answer, package):
        recovery = local_executor(
            prompt,
            _recovery_system_message(package),
            task=OC5_TASK,
            timeout=180,
            json_mode=False,
        )
        if not recovery.get("ok"):
            return OperationalContextRuntimeResult(False, "local", OC5_LOCAL_MODEL, SAFE_RUNTIME_RESPONSE_MESSAGE, prompt_tokens)
        raw_answer = str(recovery.get("answer") or "")
        if _requires_recovery(raw_answer, package):
            return OperationalContextRuntimeResult(False, "local", OC5_LOCAL_MODEL, SAFE_RUNTIME_RESPONSE_MESSAGE, prompt_tokens)
    answer = _user_facing_answer(raw_answer, package)
    return OperationalContextRuntimeResult(True, "local", str(response.get("model") or OC5_LOCAL_MODEL), answer, prompt_tokens)


def _user_facing_answer(answer: str, package: OperationalContextPackage) -> str:
    """Hide only recognized internal OC runtime envelopes, not requested JSON."""
    try:
        payload = json.loads(answer)
    except (TypeError, json.JSONDecodeError):
        return answer
    if not isinstance(payload, dict):
        return answer
    if set(payload) == {"status"} and payload.get("status") == "missing":
        return _authoritative_delivery_status(package) or SAFE_MISSING_CONTEXT_MESSAGE
    if _user_requested_json(package) and not _is_internal_schema_envelope(payload):
        return answer
    if _is_internal_schema_envelope(payload) and isinstance(payload.get("response_text"), str):
        response_text = payload["response_text"].strip()
        if _claims_missing_context(response_text):
            return _authoritative_delivery_status(package) or response_text
        return response_text or SAFE_RUNTIME_RESPONSE_MESSAGE
    if _is_internal_schema_envelope(payload):
        return SAFE_RUNTIME_RESPONSE_MESSAGE
    return answer


def _requires_recovery(answer: str, package: OperationalContextPackage) -> bool:
    try:
        payload = json.loads(answer)
    except (TypeError, json.JSONDecodeError):
        return not answer.strip() and not _user_requested_json(package)
    return isinstance(payload, dict) and _is_internal_schema_envelope(payload)


def _is_internal_schema_envelope(payload: dict[str, Any]) -> bool:
    # `response_text` alone is a legitimate JSON key a user may request.  An
    # envelope is internal only when it carries an explicit runtime failure
    # discriminator; shape similarity is never enough to hide user JSON.
    return any(payload.get(key) is True for key in ("reasoning_error", "parser_error", "schema_error"))


def _user_requested_json(package: OperationalContextPackage) -> bool:
    query = package.query.text.casefold()
    return "json" in query or "джсон" in query


def _runtime_system_message(package: OperationalContextPackage) -> str:
    if _user_requested_json(package):
        return OC5_SYSTEM_MESSAGE + " Пользователь явно запросил JSON: верни только запрошенный JSON без внутреннего envelope."
    return OC5_SYSTEM_MESSAGE + " Верни только естественный ответ на русском языке."


def _recovery_system_message(package: OperationalContextPackage) -> str:
    if _user_requested_json(package):
        return "Верни только запрошенный пользователем JSON без внутреннего envelope или служебной ошибки."
    return OC5_RECOVERY_SYSTEM_MESSAGE


def _authoritative_delivery_status(package: OperationalContextPackage) -> str:
    """Return an exact confirmed status only when the internal envelope denied it."""
    for item in package.current_authority:
        if item.get("subject_ref") == "delivery_status" and isinstance(item.get("value"), str) and item["value"].strip():
            return f"Текущий статус поставки: {item['value'].strip()}"
    return ""


def _claims_missing_context(value: str) -> bool:
    folded = " ".join(value.casefold().split())
    return "нет подтвержден" in folded or "отсутствует подтвержден" in folded


def _json_default(value: object) -> Any:
    if hasattr(value, "as_dict"):
        return value.as_dict()  # type: ignore[no-any-return]
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    raise TypeError(f"unsupported OC-5 value: {type(value)!r}")
