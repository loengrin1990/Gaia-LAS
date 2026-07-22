from __future__ import annotations

from datetime import datetime
import threading

from .archive import cleanup_run_temporary_artifacts, journal_path, safety_audit_path, write_run_journal
from .config import SETTINGS
from .extraction import extract_upload_text, safe_filename
from .masking import mask_with_review
from .memory import select_project_memory
from .models import AnalysisPackage, FileArtifact
from .packaging import build_prompt
from .policy import detect_concrete_pii, initial_policy_notes
from .profiles import get_profile


class PackageCancelledError(RuntimeError):
    pass


def ensure_not_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise PackageCancelledError("Обработка отменена пользователем или watchdog.")


def create_package(
    project: str,
    query: str,
    uploaded: list[tuple[str, bytes]],
    profile_id: str | None = None,
    strict_dialog_privacy: bool = False,
    cancel_event: threading.Event | None = None,
) -> AnalysisPackage:
    ensure_not_cancelled(cancel_event)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    profile = get_profile(profile_id)
    run_dir = SETTINGS.runs_dir / run_id
    upload_dir = run_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    query_mask = mask_with_review("Запрос пользователя", query, strict_dialog_privacy=strict_dialog_privacy)
    masked_query = query_mask.masked_text
    query_review = query_mask.review
    query_status = query_review.status
    query_replacements = query_review.total_replacements
    files: list[FileArtifact] = []
    file_hints: list[str] = []
    local_fallback_required = False
    policy_notes = initial_policy_notes()
    if query_status.startswith("невозможно"):
        local_fallback_required = True
        policy_notes.append("Veil недоступен для текста запроса, поэтому внешний маршрут заблокирован.")
    elif query_review.unresolved_pii or (detect_concrete_pii(query) and query_replacements == 0):
        local_fallback_required = True
        policy_notes.append(query_review.unresolved_reason or "Запрос похож на содержащий ПД, но замены не выполнены; нужен локальный маршрут или ручная проверка.")
    elif query_review.manual_confirmation_required:
        policy_notes.append("Запрос содержит тематический риск ПД; внешний маршрут доступен только после ручного просмотра очищенного пакета.")

    for filename, content in uploaded:
        ensure_not_cancelled(cancel_event)
        safe_name = safe_filename(filename)
        file_hints.append(safe_name)
        path = upload_dir / safe_name
        path.write_bytes(content)
        text, kind, note = extract_upload_text(path, run_dir, cancel_event=cancel_event)
        ensure_not_cancelled(cancel_event)
        file_mask = mask_with_review(safe_name, text)
        masked = file_mask.masked_text
        review = file_mask.review
        mask_status = review.status
        replacements = review.total_replacements
        if mask_status.startswith("невозможно"):
            local_fallback_required = True
            policy_notes.append(f"Файл {safe_name}: маскирование невозможно, внешний маршрут заблокирован.")
        elif review.unresolved_pii or (detect_concrete_pii(text) and replacements == 0):
            local_fallback_required = True
            policy_notes.append(f"Файл {safe_name}: {review.unresolved_reason or 'возможны ПД без замен; нужна ручная проверка или локальный маршрут.'}")
        elif review.manual_confirmation_required:
            policy_notes.append(f"Файл {safe_name}: тематический риск ПД; требуется ручной просмотр очищенного пакета перед внешним анализом.")
        files.append(FileArtifact(
            name=safe_name,
            kind=kind,
            stored_path=str(path),
            extraction_note=note,
            original_chars=len(text),
            masked_chars=len(masked),
            mask_status=mask_status,
            mask_replacements=replacements,
            transcript_status=note if kind == "media" else "",
            review=review.markdown,
            mask_review=review,
            masked_text=masked,
        ))

    ensure_not_cancelled(cancel_event)
    memory_selection = select_project_memory(
        project,
        masked_query,
        profile_text=f"{profile.title}\n{profile.template}",
        file_hints=file_hints,
    ) if project else None
    memory = memory_selection.text if memory_selection else ""
    memory_sources = memory_selection.sources if memory_selection else []
    evidence_plan = memory_selection.evidence_plan or [] if memory_selection else []
    memory_total_sections = memory_selection.total_sections if memory_selection else 0
    group_code = memory_selection.group_code if memory_selection else ""
    group_title = memory_selection.group_title if memory_selection else ""
    group_sections = memory_selection.group_sections if memory_selection else 0
    ensure_not_cancelled(cancel_event)
    prompt = build_prompt(
        project,
        memory,
        masked_query,
        files,
        profile.id,
        memory_sources,
        evidence_plan=evidence_plan,
        group_title=group_title,
    )
    prompt_mask = mask_with_review("Итоговый prompt после Lore", prompt)
    prompt = prompt_mask.masked_text
    prompt_review = prompt_mask.review
    if prompt_review.status.startswith("невозможно"):
        local_fallback_required = True
        policy_notes.append("Финальная проверка prompt недоступна, внешний маршрут заблокирован.")
    elif prompt_review.unresolved_pii or (detect_concrete_pii(prompt) and prompt_review.total_replacements == 0):
        local_fallback_required = True
        policy_notes.append(prompt_review.unresolved_reason or "Итоговый prompt содержит возможные ПД без надежной замены; нужен локальный маршрут.")
    elif prompt_review.total_replacements:
        policy_notes.append(f"Итоговый prompt дополнительно очищен после Lore: замен {prompt_review.total_replacements}.")
    elif prompt_review.manual_confirmation_required:
        policy_notes.append("Итоговый prompt тематически связан с ПД; требуется ручной просмотр очищенного пакета перед внешним анализом.")

    safe_for_codex = not local_fallback_required
    route = "Codex/ChatGPT после ручного подтверждения" if safe_for_codex else "Локально через LM Studio или ручная проверка"
    package = AnalysisPackage(
        run_id=run_id,
        project=project,
        profile_id=profile.id,
        profile_title=profile.title,
        route=route,
        safe_for_codex_after_confirmation=safe_for_codex,
        local_fallback_required=local_fallback_required,
        policy_notes=policy_notes,
        memory_chars=len(memory),
        memory_sources=memory_sources,
        evidence_plan=evidence_plan,
        memory_total_sections=memory_total_sections,
        query_mask_status=query_status,
        query_mask_replacements=query_replacements,
        query_mask_review=query_review,
        masked_query=masked_query,
        files=files,
        prompt=prompt,
        journal_path=journal_path(run_id),
        safety_audit_path=safety_audit_path(run_id),
        group_code=group_code,
        group_title=group_title,
        group_sections=group_sections,
        prompt_mask_review=prompt_review,
    )
    ensure_not_cancelled(cancel_event)
    try:
        write_run_journal(package)
    except Exception as exc:
        package.policy_notes.append(f"Журнал Obsidian не записан: {exc}")
    else:
        cleanup_run_temporary_artifacts(run_dir)
    return package
