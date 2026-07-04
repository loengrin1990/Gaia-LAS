from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .config import SETTINGS
from .extraction import extract_text
from .lore_assist import detect_gap_with_local_llm, rewrite_query_terms_with_local_llm
from .lore_rerank import rerank_with_local_llm
from .models import EvidenceItem, MemorySelection, MemorySource
from .projects import group_context_files, group_for_project
from .projects import project_dirs as registry_project_dirs
from .projects import project_memory_path as registry_project_memory_path
from .projects import project_names as registry_project_names


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}|[A-ZА-ЯЁ]{2,}")
WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)")
MVP_ALIAS_RE = re.compile(r"^(?:mvp|мвп)(\d+)$")
CYRILLIC_RE = re.compile(r"^[а-яё]+$")
MAX_SECTION_CHARS = 14000
DEFAULT_SELECTION_CHARS = 60000
MAX_EVIDENCE_ITEMS = 3
MAX_EVIDENCE_EXCERPT_CHARS = 1400
STOPWORDS = {
    "для", "или", "что", "как", "при", "это", "если", "над", "под", "без", "про",
    "the", "and", "with", "from", "this", "that", "или", "его", "она", "они",
    "нужно", "проект", "задача", "анализ", "ответ", "данные",
    "дай", "дать", "кратко", "краткий", "краткая", "краткую", "сводка", "сводку",
    "расскажи", "покажи", "найди", "какие", "какой", "какая", "какую", "есть",
    "известно", "информация", "вопрос", "вопросу", "теме", "темы",
}
CENTRAL_CONTEXT_HEADINGS = (
    "ядро проекта",
    "системы и границы",
    "архитектурные решения",
    "индекс памяти",
    "карта источников",
)
RELATED_CONTEXT_DIRS = {
    "10_Branches": 5,
    "20_Decisions": 4,
    "30_Open_Questions": 3,
    "40_Risks": 3,
}
SOURCE_SUMMARY_DIR = "50_Sources"
SOURCE_DIRS = {"Исходники", "Транскрипции"}
DOCUMENT_SOURCE_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".xlsx"}
NO_DATA_HEADING = "Проверка покрытия Lore"

PRIVACY_TERMS = {"пдн", "персональн", "персональные", "персональных", "персональным"}
STORAGE_TERMS = {"хран", "храним", "хранение", "хранит", "хранится", "хранятся", "сохраняет", "сохраняются"}
ARCHITECTURE_TERMS = {"архитектур", "архитектурн", "паспорт", "систем", "границы", "контур", "модель"}
FIELD_MAPPING_TERMS = {"поле", "поля", "реквизит", "реквизиты", "маппинг", "совпадают", "совпад", "соответствия"}
STATUS_TERMS = {"статус", "статусы", "стадия", "стадии", "состояние", "состояния"}


@dataclass(frozen=True)
class IndexedSection:
    project: str
    path: str
    heading: str
    line_start: int
    line_end: int
    text: str
    tokens: set[str]
    scope: str = "project"


INDEX_CACHE: dict[str, tuple[tuple[tuple[str, int, int], ...], list[IndexedSection]]] = {}


def project_dirs() -> list[Path]:
    return registry_project_dirs()


def project_names() -> list[str]:
    return registry_project_names()


def read_project_memory(project: str) -> str:
    project_dir = safe_project_dir(project)
    if project_dir is None:
        return ""
    path = project_memory_path(project_dir)
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def select_project_memory(
    project: str,
    query: str,
    profile_text: str = "",
    file_hints: list[str] | None = None,
    max_chars: int = DEFAULT_SELECTION_CHARS,
    max_sections: int = 8,
) -> MemorySelection:
    group = group_for_project(project)
    group_sections = group_memory_sections(group.code, group.title) if group else []
    sections = group_sections + project_memory_sections(project)
    indexed_projects = project_names()
    if not sections:
        return MemorySelection("", [], 0, indexed_projects)

    base_focus_terms = query_focus_terms(project, query)
    rewrite_terms = query_rewrite_terms(project, query, profile_text, file_hints or [])
    focus_terms = base_focus_terms | rewrite_terms
    context_terms = expand_terms(
        remove_project_terms(tokenize(" ".join([profile_text, " ".join(file_hints or [])])), project)
    )
    terms = focus_terms | context_terms
    central_candidates = select_central_context(sections, max_sections)
    scored = [(score_section(section, terms), section) for section in sections]
    query_candidates = [
        (score, section)
        for score, section in scored
        if score > 0
    ]
    direct_focus_candidates = [
        (score, section)
        for score, section in query_candidates
        if has_lore_focus_coverage(section, base_focus_terms, rewrite_terms)
    ]
    focus_missing = bool(base_focus_terms or rewrite_terms) and not direct_focus_candidates
    if focus_terms:
        anchor_candidates = sorted(
            direct_focus_candidates,
            key=lambda item: candidate_sort_key(item),
        )
        related_candidates = related_sections(anchor_candidates, sections, terms)
        candidates = merge_candidates(anchor_candidates, related_candidates)
    elif query_candidates:
        candidates = sorted(query_candidates, key=lambda item: (-item[0], item[1].line_start))
    else:
        candidates = []
    if candidates and not focus_missing:
        candidates = semantic_rerank_candidates(
            candidates,
            query,
            profile_text,
            terms,
            focus_terms,
            max_sections,
        )

    selected: list[tuple[int, IndexedSection]] = []
    total_chars = 0
    for score, section in ([] if focus_missing else candidates):
        if score <= 0:
            continue
        if any(section_identifier(section) == section_identifier(existing) for _existing_score, existing in selected):
            continue
        clipped = clip_section(section.text)
        if selected and total_chars + len(clipped) > max_chars:
            continue
        selected.append((score, section))
        total_chars += len(clipped)
        if len(selected) >= max_sections or total_chars >= max_chars:
            break
    for score, section in ([] if focus_missing else central_candidates):
        if len(selected) >= max_sections or total_chars >= max_chars:
            break
        if any(section_identifier(section) == section_identifier(existing) for _existing_score, existing in selected):
            continue
        clipped = clip_section(section.text)
        if selected and total_chars + len(clipped) > max_chars:
            continue
        selected.append((score, section))
        total_chars += len(clipped)
        if len(selected) >= max_sections or total_chars >= max_chars:
            break

    parts: list[str] = []
    if focus_missing:
        parts.append(no_data_block(project, query, focus_terms))
    elif terms and not candidates and not central_candidates:
        parts.append(no_data_block(project, query, set()))
    sources: list[MemorySource] = []
    selected_sections: list[IndexedSection] = []
    for score, section in selected:
        matched = sorted(section.tokens & terms)[:12]
        parts.append(f"## {section.heading}\n{clip_section(section.text)}")
        selected_sections.append(section)
        sources.append(MemorySource(
            id=section_identifier(section),
            project=section.project,
            path=section.path,
            heading=section.heading,
            line_start=section.line_start,
            line_end=section.line_end,
            score=score,
            matched_terms=matched,
            scope=section.scope,
        ))
    gap_block = lore_gap_block(query, focus_terms, sources)
    if gap_block:
        parts.append(gap_block)
    evidence_plan = build_evidence_plan(
        query=query,
        selected_sections=selected_sections,
        all_sections=sections,
        terms=terms,
        focus_terms=focus_terms,
    )
    return MemorySelection(
        "\n\n".join(parts),
        sources,
        len(sections),
        indexed_projects,
        evidence_plan=evidence_plan,
        group_code=group.code if group else "",
        group_title=group.title if group else "",
        group_sections=len(group_sections),
    )


def group_memory_sections(group_code: str, group_title: str) -> list[IndexedSection]:
    paths = group_context_files(group_code)
    if not paths:
        return []
    signature = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in paths)
    cache_key = f"group:{group_code}"
    cached = INDEX_CACHE.get(cache_key)
    if cached and cached[0] == signature:
        return cached[1]
    sections: list[IndexedSection] = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        sections.extend(parse_memory_sections(f"Группа: {group_title}", path, text, scope="group"))
    INDEX_CACHE[cache_key] = (signature, sections)
    return sections


def select_central_context(sections: list[IndexedSection], max_sections: int) -> list[tuple[int, IndexedSection]]:
    limit = min(2, max_sections)
    candidates = [
        (central_context_score(section), section)
        for section in sections
        if central_context_score(section) > 0
    ]
    candidates.sort(key=lambda item: (-item[0], item[1].line_start))
    return candidates[:limit]


def central_context_score(section: IndexedSection) -> int:
    heading = section.heading.lower()
    path_name = Path(section.path).stem.lower()
    score = 0
    for marker in CENTRAL_CONTEXT_HEADINGS:
        if marker in heading or marker in path_name:
            score = max(score, 4)
    if graph_folder(section) == "00_Core":
        score += 2
    return score


def is_central_context(section: IndexedSection) -> bool:
    return central_context_score(section) > 0


def related_sections(
    anchors: list[tuple[int, IndexedSection]],
    sections: list[IndexedSection],
    terms: set[str],
) -> list[tuple[int, IndexedSection]]:
    link_titles: set[str] = set()
    for _score, section in anchors[:3]:
        link_titles.update(link.strip().lower() for link in WIKI_LINK_RE.findall(section.text))
    related: list[tuple[int, IndexedSection]] = []
    for section in sections:
        heading = section.heading.lower()
        path_name = Path(section.path).stem.lower()
        folder = graph_folder(section)
        linked = any(title and (title in heading or title in path_name) for title in link_titles)
        scored = score_section(section, terms)
        if linked:
            related.append((max(scored, 8), section))
        elif folder in RELATED_CONTEXT_DIRS and scored > 0:
            related.append((scored, section))
    related.sort(key=lambda item: (-item[0], item[1].line_start))
    return related


def merge_candidates(*candidate_groups: list[tuple[int, IndexedSection]]) -> list[tuple[int, IndexedSection]]:
    merged: dict[str, tuple[int, IndexedSection]] = {}
    for group in candidate_groups:
        for score, section in group:
            section_id = section_identifier(section)
            previous = merged.get(section_id)
            if previous is None or score > previous[0]:
                merged[section_id] = (score, section)
    return sorted(merged.values(), key=candidate_sort_key)


def query_rewrite_terms(project: str, query: str, profile_text: str, file_hints: list[str]) -> set[str]:
    if not getattr(SETTINGS, "lore_query_rewrite", False):
        return set()
    timeout = int(getattr(SETTINGS, "lore_query_rewrite_timeout_seconds", 4) or 4)
    rewritten = rewrite_query_terms_with_local_llm(
        query=query,
        project=project,
        profile_text=profile_text,
        file_hints=file_hints,
        timeout=timeout,
    )
    if not rewritten:
        return set()
    return expand_terms(remove_project_terms(tokenize(" ".join(rewritten)), project))


def has_lore_focus_coverage(section: IndexedSection, base_focus_terms: set[str], rewrite_terms: set[str]) -> bool:
    if has_focus_coverage(section, base_focus_terms):
        return True
    if rewrite_terms and has_focus_coverage(section, rewrite_terms):
        return True
    return False


def lore_gap_block(query: str, focus_terms: set[str], sources: list[MemorySource]) -> str:
    if not sources or not getattr(SETTINGS, "lore_gap_detector", False):
        return ""
    timeout = int(getattr(SETTINGS, "lore_gap_detector_timeout_seconds", 4) or 4)
    gap = detect_gap_with_local_llm(
        query=query,
        sources=[source.__dict__ for source in sources],
        focus_terms=sorted(focus_terms),
        timeout=timeout,
    )
    if gap is None:
        return ""
    status = gap.get("status")
    notes = gap.get("notes") or []
    missing_terms = gap.get("missing_terms") or []
    if status == "ok" and not notes and not missing_terms:
        return ""
    lines = [
        "## Диагностика покрытия Lore",
        f"Статус покрытия: {status}.",
        "Это служебная оценка выбранных источников, а не дополнительный источник фактов.",
    ]
    if notes:
        lines.append("")
        lines.append("Наблюдения:")
        lines.extend(f"- {note}" for note in notes)
    if missing_terms:
        lines.append("")
        lines.append("Возможные недостающие термины/аспекты:")
        lines.extend(f"- {term}" for term in missing_terms)
    return "\n".join(lines)


def build_evidence_plan(
    query: str,
    selected_sections: list[IndexedSection],
    all_sections: list[IndexedSection],
    terms: set[str],
    focus_terms: set[str],
) -> list[EvidenceItem]:
    if not selected_sections:
        return [EvidenceItem(
            claim=query or "Запрос пользователя",
            status="missing",
            source_id="",
            source_path="",
            heading="",
            excerpt="",
            reason="Lore не выбрал разделы памяти, поэтому первичный evidence-layer не запускался.",
        )]

    evidence: list[EvidenceItem] = []
    used_paths: set[str] = set()
    primary_selected = [
        section for section in selected_sections
        if is_primary_source_file(Path(section.path)) and has_evidence_coverage(section, focus_terms or terms)
    ]
    for section in sorted(primary_selected, key=lambda item: (-source_authority(item), item.line_start)):
        if section.path in used_paths:
            continue
        evidence.append(evidence_from_section(
            section,
            query,
            terms,
            "confirmed",
            "Первичный источник выбран Lore и содержит прямые термины запроса.",
        ))
        used_paths.add(section.path)
        if len(evidence) >= MAX_EVIDENCE_ITEMS:
            return evidence

    raw_candidates = [
        section for section in all_sections
        if is_primary_source_file(Path(section.path))
        and section.path not in used_paths
        and has_evidence_coverage(section, focus_terms or terms)
    ]
    raw_candidates.sort(key=lambda section: (
        -score_section(section, terms),
        -source_authority(section),
        section.line_start,
    ))
    for section in raw_candidates:
        evidence.append(evidence_from_section(
            section,
            query,
            terms,
            "confirmed",
            "Первичный источник найден точечным drill-down поверх выбранной памяти.",
        ))
        used_paths.add(section.path)
        if len(evidence) >= MAX_EVIDENCE_ITEMS:
            return evidence

    source_summaries = [section for section in selected_sections if is_source_summary(section)]
    for section in source_summaries[:MAX_EVIDENCE_ITEMS - len(evidence)]:
        evidence.append(evidence_from_section(
            section,
            query,
            terms,
            "partial",
            "Есть source-summary, но первичный файл не найден среди прямых evidence-кандидатов.",
        ))

    if evidence:
        return evidence
    best = selected_sections[0]
    return [evidence_from_section(
        best,
        query,
        terms,
        "partial",
        "Ответ опирается на выбранную память; первичный источник для точечного подтверждения не найден.",
    )]


def has_evidence_coverage(section: IndexedSection, terms: set[str]) -> bool:
    if not terms:
        return False
    matches = section.tokens & terms
    if len(terms) <= 2:
        return bool(matches)
    return len(matches) >= 2 or bool(matches & mvp_terms(terms))


def evidence_from_section(
    section: IndexedSection,
    query: str,
    terms: set[str],
    status: str,
    reason: str,
) -> EvidenceItem:
    return EvidenceItem(
        claim=query or "Запрос пользователя",
        status=status,
        source_id=section_identifier(section),
        source_path=section.path,
        heading=section.heading,
        excerpt=evidence_excerpt(section.text, terms),
        reason=reason,
        scope=section.scope,
    )


def evidence_excerpt(text: str, terms: set[str]) -> str:
    clean = " ".join(text.split())
    if len(clean) <= MAX_EVIDENCE_EXCERPT_CHARS:
        return clean
    lowered = clean.lower()
    positions = [lowered.find(term.lower()) for term in terms if term and lowered.find(term.lower()) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - MAX_EVIDENCE_EXCERPT_CHARS // 3)
    end = min(len(clean), start + MAX_EVIDENCE_EXCERPT_CHARS)
    excerpt = clean[start:end].strip()
    if start > 0:
        excerpt = "... " + excerpt
    if end < len(clean):
        excerpt += " ..."
    return excerpt


def semantic_rerank_candidates(
    candidates: list[tuple[int, IndexedSection]],
    query: str,
    profile_text: str,
    terms: set[str],
    focus_terms: set[str],
    max_sections: int,
) -> list[tuple[int, IndexedSection]]:
    if not getattr(SETTINGS, "lore_semantic_rerank", False):
        return candidates
    pool_size = max(max_sections, int(getattr(SETTINGS, "lore_rerank_candidates", 24) or 24))
    timeout = int(getattr(SETTINGS, "lore_rerank_timeout_seconds", 45) or 45)
    pool = candidates[:pool_size]
    cards = [rerank_card(score, section, terms) for score, section in pool]
    selected_ids = rerank_with_local_llm(
        query=query,
        profile_text=profile_text,
        candidates=cards,
        max_ids=max_sections,
        timeout=timeout,
    )
    if not selected_ids:
        return candidates
    by_id = {section_identifier(section): (score, section) for score, section in pool}
    selected = [by_id[source_id] for source_id in selected_ids if source_id in by_id]
    if not selected:
        return candidates
    if focus_terms and not any(has_focus_coverage(section, focus_terms) for _score, section in selected):
        return candidates
    selected_set = set(selected_ids)
    remainder = [item for item in candidates if section_identifier(item[1]) not in selected_set]
    return selected + remainder


def rerank_card(score: int, section: IndexedSection, terms: set[str]) -> dict[str, object]:
    path = Path(section.path)
    return {
        "id": section_identifier(section),
        "heading": section.heading,
        "scope": section.scope,
        "path_hint": "/".join(path.parts[-4:]),
        "score": score,
        "matched_terms": sorted(section.tokens & terms)[:12],
        "excerpt": clip_section(section.text),
    }


def candidate_sort_key(item: tuple[int, IndexedSection]) -> tuple[int, int, int]:
    score, section = item
    return (-(score + source_authority(section) // 4), -source_authority(section), section.line_start)


def no_data_block(project: str, query: str, focus_terms: set[str]) -> str:
    subject = ", ".join(sorted(focus_terms)) or "запрошенной теме"
    return (
        f"## {NO_DATA_HEADING}\n"
        f"По запросу `{query or 'Запрос пуст.'}` в базе проекта `{project}` "
        f"нет подтвержденного контекста "
        f"для: {subject}.\n\n"
        "Правило ответа: не придумывать факты и не переносить сведения из соседних тем. "
        "Если пользователь спрашивает именно об этой теме, нужно прямо сообщить, "
        "что в проектной базе нет подтвержденной информации."
    )


def project_memory_sections(project: str) -> list[IndexedSection]:
    memory_path = safe_project_memory_path(project)
    if memory_path is None or not memory_path.exists():
        return []
    paths = project_memory_files(memory_path)
    signature = tuple(
        (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        for path in paths
    )
    cache_key = str(memory_path)
    cached = INDEX_CACHE.get(cache_key)
    if cached and cached[0] == signature:
        return cached[1]
    sections: list[IndexedSection] = []
    for path in paths:
        if is_primary_source_file(path):
            source_section = parse_primary_source(project, path)
            if source_section is not None:
                sections.append(source_section)
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        sections.extend(parse_memory_sections(project, path, text, scope=scope_for_path(path)))
    INDEX_CACHE[cache_key] = (signature, sections)
    return sections


def project_memory_files(memory_path: Path) -> list[Path]:
    files = [memory_path]
    project_dir = memory_path.parent
    code = project_code_from_memory(memory_path)
    for path in project_registry_files(project_dir, code):
        if path not in files:
            files.append(path)
    graph_root = project_dir / "Память_Graph"
    if graph_root.exists() and graph_root.is_dir():
        graph_files = sorted(
            path for path in graph_root.rglob("*.md")
            if path.is_file() and "90_Archive" not in path.relative_to(graph_root).parts
        )
        files.extend(graph_files)
    files.extend(project_source_files(project_dir))
    return files


def project_code_from_memory(memory_path: Path) -> str:
    marker = " - Память"
    stem = memory_path.stem
    if marker in stem:
        return stem.split(marker, 1)[0].strip()
    return ""


def project_registry_files(project_dir: Path, code: str) -> list[Path]:
    candidates: list[Path] = []
    if code:
        candidates.extend([
            project_dir / f"{code} - Источники.md",
            project_dir / f"{code} - Журнал памяти.md",
        ])
    candidates.extend([
        project_dir / "Источники.md",
        project_dir / "Журнал памяти.md",
    ])
    return [path for path in candidates if path.exists() and path.is_file()]


def project_source_files(project_dir: Path) -> list[Path]:
    files: list[Path] = []
    for source_dir_name in SOURCE_DIRS:
        root = project_dir / source_dir_name
        if not root.exists() or not root.is_dir():
            continue
        files.extend(sorted(
            path for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in DOCUMENT_SOURCE_EXTENSIONS
        ))
    return files


def is_primary_source_file(path: Path) -> bool:
    return bool(set(path.parts) & SOURCE_DIRS)


def scope_for_path(path: Path) -> str:
    name = path.name.lower()
    if "источники" in name:
        return "registry"
    if "журнал" in name:
        return "journal"
    if is_primary_source_file(path):
        return "source"
    if "Память_Graph" in path.parts:
        return "graph"
    return "project"


def parse_primary_source(project: str, path: Path) -> IndexedSection | None:
    text = read_primary_source_text(path)
    if not text.strip():
        return None
    heading = path.stem
    lines = [f"# {heading}", "", text.strip()]
    return build_section(project, path, heading, 1, len(text.splitlines()) or 1, lines, scope=scope_for_path(path))


def read_primary_source_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix in {".pdf", ".docx", ".xlsx"}:
        try:
            extracted = extract_text(path)
            if extracted is None:
                return ""
            text, _note = extracted
            return text
        except Exception:
            return ""
    return ""


def safe_project_memory_path(project: str) -> Path | None:
    project_dir = safe_project_dir(project)
    if project_dir is None:
        return None
    return project_memory_path(project_dir)


def safe_project_dir(project: str) -> Path | None:
    if not project or "/" in project or "\\" in project:
        return None
    projects_root = SETTINGS.projects.resolve()
    path = (SETTINGS.projects / project).resolve()
    try:
        if not path.is_relative_to(projects_root):
            return None
    except AttributeError:
        if projects_root not in path.parents:
            return None
    return path


def project_memory_path(project_dir: Path) -> Path | None:
    return registry_project_memory_path(project_dir)


def parse_memory_sections(project: str, path: Path, text: str, scope: str = "project") -> list[IndexedSection]:
    lines = text.splitlines()
    headings: list[tuple[int, str]] = []
    for index, line in enumerate(lines, start=1):
        match = HEADING_RE.match(line)
        if match:
            headings.append((index, match.group(2).strip()))
    if not headings and text.strip():
        return [build_section(project, path, "Память проекта", 1, len(lines), lines, scope=scope)]

    sections: list[IndexedSection] = []
    for position, (line_start, heading) in enumerate(headings):
        next_line = headings[position + 1][0] if position + 1 < len(headings) else len(lines) + 1
        section_lines = lines[line_start - 1:next_line - 1]
        sections.append(build_section(project, path, heading, line_start, next_line - 1, section_lines, scope=scope))
    return sections


def build_section(
    project: str,
    path: Path,
    heading: str,
    line_start: int,
    line_end: int,
    lines: list[str],
    scope: str = "project",
) -> IndexedSection:
    text = "\n".join(lines).strip()
    tokens = expand_terms(tokenize(f"{heading}\n{text}\n{path.stem}"))
    return IndexedSection(
        project=project,
        path=str(path),
        heading=heading,
        line_start=line_start,
        line_end=line_end,
        text=text,
        tokens=tokens,
        scope=scope,
    )


def section_identifier(section: IndexedSection) -> str:
    raw = f"{section.project}\n{section.path}\n{section.heading}\n{section.line_start}\n{section.line_end}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw_token in TOKEN_RE.findall(text):
        token = raw_token.lower()
        if token in STOPWORDS or token.isdigit():
            continue
        tokens.add(token)
        stem = russian_light_stem(token)
        if stem and stem not in STOPWORDS:
            tokens.add(stem)
    return tokens


def russian_light_stem(token: str) -> str:
    if not CYRILLIC_RE.match(token) or len(token) < 6:
        return ""
    for ending in (
        "иями", "ями", "ами", "ого", "ему", "ими", "ыми", "ией", "ия",
        "ый", "ий", "ой", "ая", "ое", "ые", "ым", "им", "ом", "ем", "ах", "ях",
        "ых", "их", "ую", "юю", "ей", "ии", "ции",
    ):
        if token.endswith(ending) and len(token) - len(ending) >= 4:
            return token[: -len(ending)]
    return ""


def query_focus_terms(project: str, query: str) -> set[str]:
    terms = expand_terms(remove_project_terms(tokenize(query), project))
    return {term for term in terms if term not in STOPWORDS}


def expand_terms(terms: set[str]) -> set[str]:
    expanded = set(terms)
    for term in list(terms):
        match = MVP_ALIAS_RE.match(term)
        if match:
            number = match.group(1)
            expanded.add(f"mvp{number}")
            expanded.add(f"мвп{number}")
        if term.startswith(("персональн", "пдн")):
            expanded.update(PRIVACY_TERMS)
        if term.startswith(("хран", "храним", "сохран")):
            expanded.update(STORAGE_TERMS)
        if term.startswith(("архитект", "паспорт", "систем", "контур")):
            expanded.update(ARCHITECTURE_TERMS)
    return expanded


def remove_project_terms(terms: set[str], project: str) -> set[str]:
    project_terms = tokenize(project)
    if not project_terms:
        return terms
    filtered: set[str] = set()
    for term in terms:
        if any(term == project_term or same_project_word(term, project_term) for project_term in project_terms):
            continue
        filtered.add(term)
    return filtered


def same_project_word(term: str, project_term: str) -> bool:
    prefix_len = min(len(term), len(project_term), 8)
    return prefix_len >= 6 and term[:prefix_len] == project_term[:prefix_len]


def score_section(section: IndexedSection, terms: set[str]) -> int:
    if section.heading.strip().lower() == "graph links":
        return 0
    if not terms:
        return 0
    heading_tokens = tokenize(section.heading)
    matches = section.tokens & terms
    heading_matches = heading_tokens & terms
    if not matches and not heading_matches:
        return 0
    score = len(matches) + len(heading_matches) * 3
    folder = graph_folder(section)
    if is_source_summary(section):
        score += 10
        if heading_matches:
            score += 12
    elif folder in RELATED_CONTEXT_DIRS:
        score += RELATED_CONTEXT_DIRS[folder]
    if is_primary_architecture_source(section) and is_privacy_storage_intent(terms):
        score += 24
    elif is_primary_source_file(Path(section.path)):
        score += 6
    if is_decision_or_core(section) and is_architecture_intent(terms):
        score += 8
    if is_field_mapping_intent(terms) and is_field_mapping_section(section):
        score += 24
        if is_direct_field_mapping_section(section):
            score += 18
        if is_status_mapping_section(section) and not is_status_intent(terms):
            score = max(1, score - 18)
    if is_correspondence_report(section) and is_privacy_storage_intent(terms):
        score = max(1, score - 8)
    return score


def source_authority(section: IndexedSection) -> int:
    if is_primary_architecture_source(section):
        return 50
    if is_decision_or_core(section):
        return 42
    if is_source_summary(section):
        return 36
    if graph_folder(section) in RELATED_CONTEXT_DIRS:
        return 28
    if is_primary_source_file(Path(section.path)):
        return 24
    if section.scope == "registry":
        return 18
    if section.scope == "journal":
        return 10
    return 16


def is_primary_architecture_source(section: IndexedSection) -> bool:
    if not is_primary_source_file(Path(section.path)):
        return False
    text = f"{section.heading}\n{Path(section.path).stem}".lower()
    return any(marker in text for marker in ("паспорт", "архитект", "систем", "границ"))


def is_decision_or_core(section: IndexedSection) -> bool:
    folder = graph_folder(section)
    return folder in {"00_Core", "20_Decisions"} or any(
        marker in section.heading.lower()
        for marker in ("архитектурные решения", "системы и границы", "ядро проекта")
    )


def is_correspondence_report(section: IndexedSection) -> bool:
    text = f"{section.heading}\n{Path(section.path).stem}".lower()
    return "отчет" in text and any(marker in text for marker in ("переписк", "исполнител", "коммуникац"))


def is_privacy_storage_intent(terms: set[str]) -> bool:
    return bool(terms & PRIVACY_TERMS) and bool(terms & STORAGE_TERMS)


def is_architecture_intent(terms: set[str]) -> bool:
    return bool(terms & ARCHITECTURE_TERMS) or is_privacy_storage_intent(terms)


def is_field_mapping_intent(terms: set[str]) -> bool:
    return bool(terms & FIELD_MAPPING_TERMS) and bool({"бф", "до"} <= terms)


def is_status_intent(terms: set[str]) -> bool:
    return bool(terms & STATUS_TERMS)


def is_field_mapping_section(section: IndexedSection) -> bool:
    text = f"{section.heading}\n{Path(section.path).stem}\n{section.text[:2000]}".lower()
    has_systems = "бф" in section.tokens and "до" in section.tokens
    has_field_language = any(marker in text for marker in ("поле", "реквизит", "маппинг", "источник в до"))
    return has_systems and has_field_language


def is_direct_field_mapping_section(section: IndexedSection) -> bool:
    text = section.text[:2500].lower()
    return "поле бф" in text and "источник в до" in text


def is_status_mapping_section(section: IndexedSection) -> bool:
    text = f"{section.heading}\n{Path(section.path).stem}".lower()
    return any(marker in text for marker in ("статус", "стадия", "состояние"))


def has_focus_coverage(section: IndexedSection, focus_terms: set[str]) -> bool:
    if not focus_terms:
        return False
    if len(focus_terms) <= 2:
        return bool(section.tokens & focus_terms)
    matches = section.tokens & focus_terms
    return len(matches) >= 2 or bool(matches & mvp_terms(focus_terms))


def mvp_terms(terms: set[str]) -> set[str]:
    return {term for term in terms if MVP_ALIAS_RE.match(term)}


def is_source_summary(section: IndexedSection) -> bool:
    return graph_folder(section) == SOURCE_SUMMARY_DIR and "карта источников" not in section.heading.lower()


def graph_folder(section: IndexedSection) -> str:
    path = Path(section.path)
    parts = path.parts
    if "Память_Graph" not in parts:
        return ""
    graph_index = parts.index("Память_Graph")
    if graph_index + 1 >= len(parts):
        return ""
    return parts[graph_index + 1]


def clip_section(text: str) -> str:
    if len(text) <= MAX_SECTION_CHARS:
        return text
    return text[:MAX_SECTION_CHARS].rstrip() + "\n\n[...section clipped by Lore...]"
