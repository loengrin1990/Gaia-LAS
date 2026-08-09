from __future__ import annotations

from .context_assembler import DialogueContext, TrustedContextItem
from .models import EvidenceItem, FileArtifact, MemorySource
from .profiles import get_profile


def build_prompt(
    project: str,
    memory: str,
    masked_query: str,
    files: list[FileArtifact],
    profile_id: str | None = None,
    memory_sources: list[MemorySource] | None = None,
    evidence_plan: list[EvidenceItem] | None = None,
    group_title: str = "",
    dialogue_context: DialogueContext | None = None,
) -> str:
    profile = get_profile(profile_id)
    file_parts = []
    for item in files:
        if item.masked_text:
            file_parts.append(f"## Файл: {item.name}\n{item.masked_text[:30000]}")
        else:
            details = [
                f"тип: {item.kind or 'unknown'}",
                f"извлечение: {item.extraction_note or 'текст не извлечен'}",
                f"исходный текст: {item.original_chars} зн.",
                f"после локальной обработки: {item.masked_chars} зн.",
                f"маскирование: {item.mask_status or '-'}",
            ]
            file_parts.append(f"## Файл: {item.name}\nФайл приложен, но текст для анализа пустой.\n" + "\n".join(f"- {detail}" for detail in details))
    file_block = "\n\n".join(file_parts) or "Файлы не приложены."
    memory_block = dialogue_context.memory_text if dialogue_context else memory[:60000]
    memory_block = memory_block or "Lore не выбрал релевантный материал памяти."
    sources_block = format_memory_sources(memory_sources or [])
    evidence_block = format_evidence_plan(evidence_plan or [])
    group_line = f"\n# Группа контекста\n{group_title}\n" if group_title else ""
    dialogue_layers = _dialogue_layers(dialogue_context, memory_block)
    return (
        "Ты работаешь с безопасно подготовленным локальным аналитическим пакетом.\n"
        "Не проси исходные ПД. Если данных недостаточно, сформулируй локальный шаг проверки.\n\n"
        "# Контракт работы с эффективным контекстом\n"
        "- Отвечай только по групповому и проектному контексту, выбранному Lore, и приложенным локально обработанным материалам.\n"
        "- Групповой контекст задает общие регламенты, шаблоны, методики и ограничения для нескольких проектов.\n"
        "- При конфликте проектная память имеет приоритет над групповым контекстом; конфликт нужно явно назвать.\n"
        "- Не придумывай этапы, решения, метрики, источники или статусы, если их нет в выбранной памяти.\n"
        "- Не переноси сведения из похожих тем, соседних MVP, рисков или открытых вопросов на запрошенную тему.\n"
        "- Если в блоке `Проверка покрытия Lore` сказано, что подтвержденного контекста нет, прямо сообщи пользователю, что в базе проекта нет информации по вопросу.\n"
        "- Учитывай `Evidence plan`: confirmed excerpts можно использовать как подтверждение; partial/missing нужно явно трактовать как неполное покрытие.\n"
        "- Явно отделяй проверенные факты от предположений; открытые вопросы не считай решениями, а риски не считай фактами.\n\n"
        f"# Профиль задачи\n{profile.title}\n\n"
        f"## Инструкция профиля\n{profile.template}\n\n"
        f"# Проект\n{project}\n\n"
        f"{group_line}"
        f"{dialogue_layers}\n"
        f"# Источники выбора Lore\n{sources_block}\n\n"
        f"# Evidence plan Lore\n{evidence_block}\n\n"
        f"# Запрос пользователя, после локальной обработки\n{masked_query or 'Запрос пуст.'}\n\n"
        f"# Материалы, после локальной обработки\n{file_block}\n"
    )


def _dialogue_layers(dialogue_context: DialogueContext | None, memory_block: str) -> str:
    if dialogue_context is None:
        return f"# Эффективный контекст, выбранный Lore\n{memory_block}\n\n"
    return (
        "# Текущий операционный контекст\n"
        "Этот слой содержит только проверенные, подтверждённые и актуальные записи проекта. "
        "Он является текущим источником истины для состояния проекта.\n"
        "Если он расходится с памятью, используй его для трактовки текущего состояния; "
        "память сохраняй как историю, обоснование и более широкий материал. Не объединяй и не удаляй сведения автоматически.\n"
        f"{format_trusted_context(dialogue_context.current_authority)}\n\n"
        "# Память проекта, выбранная Lore\n"
        "Это знания, история, обоснование и связанный материал; она не считается автоматически актуальным состоянием.\n"
        f"{memory_block}\n\n"
    )


def format_trusted_context(items: tuple[TrustedContextItem, ...]) -> str:
    if not items:
        return "Подтверждённый актуальный операционный контекст по запросу не найден."
    lines = []
    for item in items:
        provenance = ", ".join(
            value for value in (
                f"id: {item.id}" if item.id else "",
                f"sources: {', '.join(item.source_links)}" if item.source_links else "",
                f"parents: {', '.join(item.parents)}" if item.parents else "",
                f"blocks: {len(item.block_links)}" if item.block_links else "",
            ) if value
        ) or "provenance: отсутствует"
        details = "; ".join(
            value for value in (
                f"ответственный: {item.actor_ref}" if item.actor_ref else "",
                f"срок: {item.deadline}" if item.deadline else "",
                f"статус: {item.explicit_status}" if item.explicit_status else "",
                f"приоритет: {item.priority}" if item.priority else "",
            ) if value
        )
        lines.append(f"- [{item.item_type}] {item.title}\n  {item.statement}\n  {details}\n  provenance: {provenance}".rstrip())
    return "\n".join(lines)


def format_memory_sources(sources: list[MemorySource]) -> str:
    if not sources:
        return "Lore не выбрал разделы памяти."
    lines = []
    for source in sources:
        terms = ", ".join(source.matched_terms) if source.matched_terms else "нет"
        lines.append(
            f"- {source.heading} ({source.scope}: {source.project}, строки {source.line_start}-{source.line_end}, "
            f"score {source.score}, совпадения: {terms})"
        )
    return "\n".join(lines)


def format_evidence_plan(items: list[EvidenceItem]) -> str:
    if not items:
        return "Evidence drill-down не запускался или не нашел дополнительных подтверждений."
    lines = []
    for item in items:
        path_hint = item.source_path or "-"
        excerpt = item.excerpt or "-"
        lines.append(
            f"- status: {item.status}; heading: {item.heading or '-'}; scope: {item.scope}; "
            f"path: {path_hint}; reason: {item.reason}\n"
            f"  excerpt: {excerpt}"
        )
    return "\n".join(lines)
