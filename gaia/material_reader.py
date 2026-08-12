"""Read rich evidence from a controlled PDF source without changing its source bytes."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from .provenance import ProvenanceError, ProvenanceStore
from .storage import atomic_write_bytes, atomic_write_text


READER_VERSION = "rpm-1-pdf-v1"


class MaterialReaderError(ProvenanceError):
    pass


@dataclass(frozen=True)
class MaterialEvidence:
    evidence_id: str
    modality: str
    page: int
    locator: dict[str, Any]
    origin: str
    state: str
    text: str = ""
    image_bytes: bytes = b""


@dataclass(frozen=True)
class MaterialReadResult:
    text: str
    evidence: tuple[MaterialEvidence, ...]


class MaterialReader:
    """Minimal PDF reader for page text, table layout, and embedded visuals.

    The reader deliberately does not infer a domain model from a diagram.  OCR
    output is retained only as an observed label/text fragment with a precise
    page/image locator.  Missing OCR is a partial result, never a fabricated
    observation and never a reason to discard readable text or tables.
    """

    def __init__(self, ocr_command: str = "tesseract", ocr_timeout_seconds: int = 20) -> None:
        self.ocr_command = ocr_command
        self.ocr_timeout_seconds = ocr_timeout_seconds

    def create_pdf_extraction(self, store: ProvenanceStore, workspace_id: str, source_id: str) -> dict[str, Any]:
        if not store.verify_source(workspace_id, source_id):
            raise MaterialReaderError("Материал изменился и требует повторного добавления.")
        result = self.read_pdf(store.source_path(workspace_id, source_id).read_bytes())
        extraction = store._versioned_content(  # Existing versioning/storage seam; source remains immutable.
            workspace_id, "extraction", "art", result.text, source_id, READER_VERSION, ""
        )
        self._persist(store, workspace_id, source_id, extraction["artifact_id"], result.evidence)
        return extraction

    def read_pdf(self, content: bytes) -> MaterialReadResult:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise MaterialReaderError("PDF-обработка недоступна: не установлена обязательная библиотека.") from exc
        try:
            reader = PdfReader(BytesIO(content))
        except Exception as exc:
            raise MaterialReaderError("Не удалось прочитать PDF как управляемый материал.") from exc

        evidence: list[MaterialEvidence] = []
        text_parts: list[str] = []
        for page_number, page in enumerate(reader.pages, 1):
            page_text_parts: list[str] = []
            try:
                native_text = page.extract_text(extraction_mode="layout") or ""
            except Exception:
                native_text = ""
            if native_text.strip():
                evidence.append(MaterialEvidence(
                    f"ev_text_p{page_number}", "text", page_number,
                    {"kind": "page", "page": page_number}, "native_pdf_text", "ready", native_text,
                ))
                # The legacy Veil/context path accepts text, not a layout tree.
                # Keep the stronger page layout as evidence above, while giving
                # that existing path one bounded paragraph per page.
                page_text_parts.append(_context_text(native_text))
                table_text = _table_layout(native_text)
                if table_text:
                    evidence.append(MaterialEvidence(
                        f"ev_table_p{page_number}", "table_layout", page_number,
                        {"kind": "page_layout", "page": page_number}, "native_pdf_layout", "ready", table_text,
                    ))
            try:
                images = list(page.images)
            except Exception:
                images = []
            for image_index, image in enumerate(images, 1):
                buffer = BytesIO()
                try:
                    image.image.save(buffer, format="PNG")
                    image_bytes = buffer.getvalue()
                except Exception:
                    evidence.append(MaterialEvidence(
                        f"ev_visual_p{page_number}_{image_index}", "visual", page_number,
                        {"kind": "embedded_image", "page": page_number, "image_index": image_index},
                        "embedded_pdf_image", "error",
                    ))
                    continue
                state, observed = self._ocr(image_bytes)
                if state == "ready":
                    observed = "\n".join(filter(None, (observed, self._layout_observations(image_bytes))))
                evidence.append(MaterialEvidence(
                    f"ev_visual_p{page_number}_{image_index}", "visual", page_number,
                    {"kind": "embedded_image", "page": page_number, "image_index": image_index},
                    "embedded_pdf_image", state, observed, image_bytes,
                ))
                if state == "ready" and observed.strip():
                    page_text_parts.append(_context_text(observed))
            if page_text_parts:
                text_parts.append(" ".join(page_text_parts))

        if not text_parts:
            raise MaterialReaderError("PDF не содержит доступного текстового или визуального представления.")
        return MaterialReadResult("\n\n".join(text_parts) + "\n", tuple(evidence))

    def supporting_evidence(self, store: ProvenanceStore, workspace_id: str, context_id: str) -> list[dict[str, Any]]:
        """Return safe source fragments supporting a stored context candidate.

        A link is emitted only for an exact contained fragment or at least three
        independently shared terms.  This is corroboration, not a new factual
        inference; unavailable/failed modalities never become support.
        """
        context = store.object_metadata(workspace_id, context_id)
        if context.get("kind") != "context":
            raise MaterialReaderError("Элемент контекста недоступен в этом рабочем пространстве.")
        sanitized_id = (context.get("parents") or [""])[0]
        sanitized = store.object_metadata(workspace_id, sanitized_id)
        extraction_id = (sanitized.get("parents") or [""])[0]
        manifest = self._manifest(store, workspace_id, extraction_id)
        if not manifest:
            return []
        explicit_ids = context.get("material_evidence_ids")
        if isinstance(explicit_ids, list) and explicit_ids:
            by_id = {item.get("evidence_id"): item for item in manifest.get("evidence", [])}
            result = []
            for evidence_id in explicit_ids:
                item = by_id.get(evidence_id)
                if not isinstance(evidence_id, str) or not item or item.get("state") != "ready":
                    continue
                result.append({
                    "evidence_id": item["evidence_id"], "modality": item["modality"], "page": item["page"],
                    "locator": item["locator"], "origin": item["origin"], "support": "explicit_reference",
                })
            return result
        body = (store.root / "sanitized" / workspace_id / f"{sanitized_id}.txt").read_text(encoding="utf-8")
        fragments = []
        for link in context.get("block_links") or []:
            if not isinstance(link, dict):
                continue
            start, end = link.get("start"), link.get("end")
            if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(body):
                fragments.append(body[start:end])
        selected = "\n".join(fragments)
        selected_terms = _terms(selected)
        result: list[dict[str, Any]] = []
        for item in manifest.get("evidence", []):
            if item.get("state") != "ready" or not isinstance(item.get("text"), str):
                continue
            evidence_text = item["text"].strip()
            shared_terms = sorted(selected_terms & _terms(evidence_text))
            exact = bool(selected and selected in evidence_text)
            if exact or len(shared_terms) >= 3:
                result.append({
                    "evidence_id": item["evidence_id"], "modality": item["modality"], "page": item["page"],
                    "locator": item["locator"], "origin": item["origin"],
                    "support": "exact_fragment" if exact else "shared_terms",
                })
        return result

    def _ocr(self, image_bytes: bytes) -> tuple[str, str]:
        if not shutil.which(self.ocr_command):
            return "unsupported", ""
        try:
            result = subprocess.run(
                [self.ocr_command, "stdin", "stdout", "-l", "eng", "--psm", "11"],
                input=image_bytes, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=self.ocr_timeout_seconds, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "error", ""
        if result.returncode != 0:
            return "error", ""
        observed = result.stdout.decode("utf-8", errors="replace").strip()
        return ("ready", observed) if observed else ("unsupported", "")

    def _layout_observations(self, image_bytes: bytes) -> str:
        """Derive generic component-in-group observations from OCR geometry.

        This recognises a component title by the nearby C4 ``[Container: ...]``
        line and a group by its ``... node group`` label.  It uses positions only;
        it has no fixture or domain-component vocabulary.
        """
        if not shutil.which(self.ocr_command):
            return ""
        try:
            result = subprocess.run(
                [self.ocr_command, "stdin", "stdout", "-l", "eng", "--psm", "11", "tsv"],
                input=image_bytes, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=self.ocr_timeout_seconds, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if result.returncode != 0:
            return ""
        words = _tsv_words(result.stdout.decode("utf-8", errors="replace"))
        groups = [word for word in words if word["text"].casefold() == "group" and any("ode" in other["text"].casefold() for other in words if abs(other["top"] - word["top"]) < 24 and 0 < word["left"] - other["left"] < 180)]
        observations: set[str] = set()
        for title in words:
            if not title["text"] or not title["text"][0].isupper():
                continue
            is_container = any(
                "container" in descriptor["text"].casefold()
                and abs(descriptor["left"] - title["left"]) < 130
                and 0 < descriptor["top"] - title["top"] < 80
                for descriptor in words
            )
            if not is_container:
                continue
            if any(
                group["left"] <= title["left"] <= group["left"] + 1300
                and 0 < group["top"] - title["top"] < 280
                for group in groups
            ):
                observations.add(f'VISUAL_LAYOUT: "{title["text"]}" is placed inside a node group.')
        return "\n".join(sorted(observations))

    def _persist(self, store: ProvenanceStore, workspace_id: str, source_id: str, extraction_id: str, evidence: tuple[MaterialEvidence, ...]) -> None:
        directory = store.root / "artifacts" / workspace_id
        directory.mkdir(parents=True, exist_ok=True)
        safe: list[dict[str, Any]] = []
        persisted: list[dict[str, Any]] = []
        for item in evidence:
            image_name = ""
            if item.image_bytes:
                image_name = f"{extraction_id}.{item.evidence_id}.png"
                atomic_write_bytes(directory / image_name, item.image_bytes)
            payload = {
                "evidence_id": item.evidence_id, "modality": item.modality, "page": item.page,
                "locator": item.locator, "origin": item.origin, "state": item.state,
                "text": item.text, "image_name": image_name,
            }
            persisted.append(payload)
            safe.append({key: payload[key] for key in ("evidence_id", "modality", "page", "locator", "origin", "state", "image_name")})
        manifest = {"reader_version": READER_VERSION, "source_id": source_id, "extraction_id": extraction_id, "evidence": persisted}
        atomic_write_text(directory / f"{extraction_id}.evidence.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        store._update(extraction_id, material_evidence=safe, reader_version=READER_VERSION)

    def _manifest(self, store: ProvenanceStore, workspace_id: str, extraction_id: str) -> dict[str, Any]:
        path = store.root / "artifacts" / workspace_id / f"{extraction_id}.evidence.json"
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if payload.get("extraction_id") == extraction_id else {}


def _table_layout(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    rows = [line for line in lines if len([part for part in re.split(r" {2,}", line.strip()) if part]) >= 3]
    return "\n".join(rows)


def _context_text(text: str) -> str:
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def _terms(value: str) -> set[str]:
    return {term.casefold() for term in re.findall(r"[A-Za-zА-Яа-яЁё0-9]{3,}", value)}


def _tsv_words(value: str) -> list[dict[str, Any]]:
    rows = []
    for line in value.splitlines()[1:]:
        fields = line.split("\t")
        if len(fields) != 12 or fields[0] != "5" or not fields[11].strip():
            continue
        try:
            rows.append({"left": int(fields[6]), "top": int(fields[7]), "text": fields[11].strip()})
        except ValueError:
            continue
    return rows
