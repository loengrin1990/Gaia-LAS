from __future__ import annotations

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
    memory_block = memory[:60000] if memory else "Эффективный контекст не найден или не выбран."
    sources_block = format_memory_sources(memory_sources or [])
    evidence_block = format_evidence_plan(evidence_plan or [])
    group_line = f"\n# Группа контекста\n{group_title}\n" if group_title else ""
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
        f"# Эффективный контекст, выбранный Lore\n{memory_block}\n\n"
        f"# Источники выбора Lore\n{sources_block}\n\n"
        f"# Evidence plan Lore\n{evidence_block}\n\n"
        f"# Запрос пользователя, после локальной обработки\n{masked_query or 'Запрос пуст.'}\n\n"
        f"# Материалы, после локальной обработки\n{file_block}\n"
    )


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
