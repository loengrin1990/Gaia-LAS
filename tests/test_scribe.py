from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.scribe import (
    BLOCK_REASON,
    apply_scribe_plan,
    create_scribe_draft,
    create_scribe_plan,
    package_has_unresolved_pii,
)


def package_fixture(prompt: str = "Обнови правила проекта.") -> dict:
    return {
        "run_id": "test-run",
        "project": "Автопретензии",
        "route": "Codex/ChatGPT после ручного подтверждения",
        "safe_for_codex_after_confirmation": True,
        "local_fallback_required": False,
        "policy_notes": ["ПД должны быть замаскированы до внешнего анализа."],
        "memory_chars": 123,
        "query_mask_status": "выполнено",
        "query_mask_replacements": 3,
        "query_mask_review": {
            "unresolved_pii": False,
            "status": "выполнено",
            "total_replacements": 3,
            "counts": {"PERSON": 1, "PHONE": 1, "EMAIL": 1},
        },
        "files": [],
        "evidence_plan": [
            {
                "status": "confirmed",
                "heading": "Паспорт системы",
                "source_path": "/tmp/passport.pdf",
                "excerpt": "Система хранит данные локально.",
            }
        ],
        "prompt": prompt,
        "journal_path": "/tmp/test-run.md",
    }


class ScribeTests(unittest.TestCase):
    def test_creates_masked_markdown_draft_and_instruction(self) -> None:
        prompt = "Итоги встречи: Иванов Иван Иванович, телефон +7 999 123-45-67, email test@example.com."
        with tempfile.TemporaryDirectory() as tmp:
            draft = create_scribe_draft(package_fixture(prompt), output_dir=Path(tmp))

            self.assertTrue(Path(draft.draft_path).exists())
            self.assertIn("$update-obsidian-project-memory", draft.instruction)
            self.assertIn("Память.md", draft.instruction)
            self.assertIn("Источники.md", draft.instruction)
            self.assertIn("Журнал памяти.md", draft.instruction)
            self.assertNotIn("Иванов Иван Иванович", draft.markdown)
            self.assertNotIn("+7 999 123-45-67", draft.markdown)
            self.assertNotIn("test@example.com", draft.markdown)
            self.assertIn("[PERSON_", draft.markdown)
            self.assertIn("[PHONE_", draft.markdown)
            self.assertIn("[EMAIL_", draft.markdown)

    def test_does_not_touch_project_memory_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "Проекты" / "Автопретензии"
            project_dir.mkdir(parents=True)
            memory_path = project_dir / "Память.md"
            memory_path.write_text("стабильная память", encoding="utf-8")

            create_scribe_draft(package_fixture(), output_dir=Path(tmp) / "drafts")

            self.assertEqual(memory_path.read_text(encoding="utf-8"), "стабильная память")

    def test_blocks_unresolved_pii_package(self) -> None:
        package = package_fixture()
        package["query_mask_review"]["unresolved_pii"] = True

        self.assertTrue(package_has_unresolved_pii(package))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, BLOCK_REASON):
                create_scribe_draft(package, output_dir=Path(tmp))

    def test_llm_classifier_adds_candidates_to_draft_only(self) -> None:
        settings = type("Settings", (), {"scribe_candidate_classifier": True, "scribe_classifier_timeout_seconds": 1})()
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("gaia.scribe.SETTINGS", settings),
                patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value={
                    "decisions": ["Зафиксировать правило маршрутизации."],
                    "rules": [],
                    "risks": ["Есть риск неполного источника."],
                    "open_questions": [],
                    "technical_facts": [],
                    "exclude": ["Разовые детали встречи."],
                }),
            ):
                draft = create_scribe_draft(package_fixture(), output_dir=Path(tmp))

        self.assertIn("LLM-классификация кандидатов", draft.markdown)
        self.assertIn("Зафиксировать правило маршрутизации.", draft.markdown)
        self.assertIn("Разовые детали встречи.", draft.markdown)

    def test_scribe_plan_builds_staged_items_without_writing_memory(self) -> None:
        settings = type("Settings", (), {"scribe_candidate_classifier": True, "scribe_classifier_timeout_seconds": 1})()
        with (
            patch("gaia.scribe.SETTINGS", settings),
            patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value={
                "decisions": ["Принято решение использовать staged review перед записью памяти."],
                "rules": [],
                "risks": [],
                "open_questions": [],
                "technical_facts": [],
                "exclude": ["Разовая фраза встречи."],
            }),
        ):
            plan = create_scribe_plan(package_fixture())

        self.assertEqual(plan.status, "ready")
        self.assertTrue(any(item.destination == "20_Decisions" for item in plan.items))
        self.assertFalse(any(item.destination == "50_Sources" for item in plan.items))
        self.assertTrue(any(item.destination == "exclude" and not item.selected for item in plan.items))
        self.assertIn("Scribe plan", plan.preview)

    def test_inbox_scribe_plan_is_scoped_to_selected_file_not_lore_evidence(self) -> None:
        package = package_fixture()
        package["scribe_origin"] = {
            "type": "inbox",
            "relative_path": "Исходники/АПР - {Бэклог}.xlsx",
            "name": "АПР - {Бэклог}.xlsx",
            "kind": "excel",
        }
        package["files"] = [
            {
                "name": "АПР - {Бэклог}.xlsx",
                "kind": "xlsx",
                "stored_path": "/tmp/АПР - {Бэклог}.xlsx",
                "extraction_note": "структурно нормализован Excel",
                "masked_text": "Excel preview: Бэклог. Заголовки: ID, Экран, Задача, Статус.",
                "mask_review": {"unresolved_pii": False},
            }
        ]

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        bodies = "\n".join(item.body for item in plan.items)
        self.assertIn("АПР - {Бэклог}.xlsx", bodies)
        self.assertIn("структурно нормализован Excel", bodies)
        self.assertNotIn("Excel preview: Бэклог", bodies)
        self.assertNotIn("Паспорт системы", bodies)
        self.assertTrue(all("АПР - {Бэклог}.xlsx" in item.evidence for item in plan.items if item.destination == "50_Sources"))

    def test_dialog_scribe_plan_uses_uploaded_files_when_classifier_is_empty(self) -> None:
        package = package_fixture()
        package["project"] = "Unit Test Project"
        package["files"] = [
            {
                "name": "meeting_audio.mp3",
                "kind": "media",
                "stored_path": "/tmp/meeting_audio.mp3",
                "extraction_note": "готово: transcript.txt",
                "masked_text": "Обсуждалась текущая архитектура и планируемая схема интеграции.",
                "mask_review": {"unresolved_pii": False},
            }
        ]
        package["evidence_plan"] = []

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        self.assertEqual(plan.status, "ready")
        self.assertTrue(any(item.destination == "50_Sources" for item in plan.items))
        self.assertTrue(any("meeting_audio.mp3" in item.body for item in plan.items))
        source_items = [item for item in plan.items if item.destination == "50_Sources"]
        self.assertFalse(any("Обсуждалась текущая архитектура" in item.body for item in source_items))
        self.assertFalse(any(item.title == "Нет кандидатов" for item in plan.items))

    def test_lore_evidence_excerpt_is_not_written_as_source_summary(self) -> None:
        package = package_fixture()
        package["files"] = []
        package["evidence_plan"] = [
            {
                "status": "confirmed",
                "heading": "АПР - АП_Полное_описание_работы_сервиса_с_ПД",
                "source_path": "/tmp/source.docx",
                "excerpt": "# Заголовок " + ("сырой текст " * 80),
            }
        ]

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        bodies = "\n".join(item.body for item in plan.items)
        self.assertNotIn("сырой текст", bodies)
        self.assertFalse(any(item.destination == "50_Sources" for item in plan.items))
        self.assertTrue(any(item.destination == "exclude" for item in plan.items))

    def test_generic_process_words_are_excluded_without_domain_pattern(self) -> None:
        package = package_fixture()
        package["project"] = "Unit Test Project"
        package["files"] = [
            {
                "name": "generic.docx",
                "kind": "docx",
                "stored_path": "/tmp/generic.docx",
                "extraction_note": "текст извлечен",
                "masked_text": "Источник содержит архитектуру, процесс, систему и отчет без конкретных доменных фактов.",
                "mask_review": {"unresolved_pii": False},
            }
        ]
        package["evidence_plan"] = []

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        fallback = [item for item in plan.items if "общие архитектурные" in item.body]
        self.assertTrue(fallback)
        self.assertTrue(all(item.destination == "exclude" and not item.selected for item in fallback))

    def test_ocr_osmi_document_extracts_domain_context(self) -> None:
        package = package_fixture()
        package["project"] = "Unit Test Project"
        package["files"] = [
            {
                "name": "Полное_описание_работы_сервиса_с_ПД.docx",
                "kind": "docx",
                "stored_path": "/tmp/Полное_описание_работы_сервиса_с_ПД.docx",
                "extraction_note": "текст извлечен из Word",
                "masked_text": (
                    "Отправляется POST /recognize. Файл pdf/docx, из него формируется meta.json. "
                    "Происходит разделение на страницы и сохранение jpg в S3. "
                    "Обработка каждой страницы Tesseract, извлечение ocr_confidence. "
                    "Извлечение ПД: GLiNER + regex, координаты на странице, повторный поиск падежных форм ФИО. "
                    "backend делает GET /pii и получает страницы, координаты и данные ПД. "
                    "OSMI получает txt с заменой всех ПД на [REDACTED], start_context.txt и jpg с закрытием ПД. "
                    "Создаются defects.json и defects.xlsx, backend сохраняет дефекты в БД."
                ),
                "mask_review": {"unresolved_pii": False},
            }
        ]
        package["evidence_plan"] = []

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        domain_items = [item for item in plan.items if item.title == "OCR OSMI ПД"]
        self.assertEqual(len(domain_items), 1)
        item = domain_items[0]
        self.assertTrue(item.selected)
        self.assertEqual(item.destination, "10_Branches")
        self.assertLessEqual(len(item.title.split()), 3)
        self.assertIn("POST /recognize", item.body)
        self.assertIn("GLiNER + regex", item.body)
        self.assertIn("defects.json", item.body)

    def test_source_summary_uses_short_memory_title_not_file_slug(self) -> None:
        package = package_fixture()
        package["files"] = [{
            "name": "АПР - Баг-лист и бэклог ФСК Экспертизы 2026-07-02.xlsx",
            "kind": "xlsx",
            "stored_path": "/tmp/АПР - Баг-лист и бэклог ФСК Экспертизы 2026-07-02.xlsx",
            "extraction_note": "структурно нормализован Excel",
            "masked_text": "Баг-лист и бэклог содержат статусы, критичность, ретест и backlog.",
            "mask_review": {"unresolved_pii": False},
        }]
        package["evidence_plan"] = []

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        source = next(item for item in plan.items if item.destination == "50_Sources")
        self.assertEqual(source.title, "Баги бэклог")
        self.assertIn("50_Sources/АПР - Баги бэклог.md", source.target_path)
        self.assertIn("АПР - Баг-лист и бэклог ФСК Экспертизы", source.body)

    def test_code_findings_source_summary_gets_contextual_body(self) -> None:
        package = package_fixture()
        package["files"] = [{
            "name": "avtopretenzii_zamechaniya_3.docx",
            "kind": "docx",
            "stored_path": "/tmp/avtopretenzii_zamechaniya_3.docx",
            "extraction_note": "текст извлечен из Word",
            "masked_text": (
                "Автопретензии. Реестр замечаний по коду. "
                "I. Обработка ошибок. Документ может навсегда зависнуть. "
                "II. Надёжность и потеря данных. Событие из CRM теряется. "
                "III. Статусы и синхронизация интерфейса. "
                "Интерфейс сам проставляет статус готов к проверке."
            ),
            "mask_review": {"unresolved_pii": False},
        }]
        package["evidence_plan"] = []

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        source = next(item for item in plan.items if item.destination == "50_Sources")
        self.assertEqual(source.title, "Замечания кода")
        self.assertIn("50_Sources/АПР - Замечания кода.md", source.target_path)
        self.assertIn("обработка ошибок", source.body)
        self.assertIn("потеря данных", source.body)
        self.assertIn("готов к проверке", source.body)

    def test_hash_named_pdf_gets_contextual_mvp2_role_summary(self) -> None:
        package = package_fixture()
        package["project"] = "Unit Test Project"
        package["files"] = [{
            "name": "418532867_c32bc21dcf96467c9cfeacacdcf811de-060726-1812-756.pdf",
            "kind": "pdf",
            "stored_path": "/tmp/418532867_c32bc21dcf96467c9cfeacacdcf811de-060726-1812-756.pdf",
            "extraction_note": "извлечен текст PDF",
            "masked_text": (
                "2. Описание ролевой модели и процесса работы MVP2. "
                "Перечень ролей: Эксперт, Расчетчик, Администратор системы. "
                "Матрица ролей включает вход в систему, управление пользователями, права доступа, "
                "загрузку документов, валидацию результата работы сервиса, направление результата на расчетчика, "
                "получение заполненной формы компенсации и отправку результата в Техзор."
            ),
            "mask_review": {"unresolved_pii": False},
        }]
        package["evidence_plan"] = []

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        source = next(item for item in plan.items if item.destination == "50_Sources")
        self.assertEqual(source.title, "Роли MVP2")
        self.assertTrue(source.selected)
        self.assertIn("50_Sources/UTP - Роли MVP2.md", source.target_path)
        self.assertIn("Эксперт, Расчетчик и Администратор", source.body)
        self.assertNotIn("418532867 c32bc21dcf96467c9cfeacac", source.title)

    def test_hash_named_pdf_gets_contextual_mvp2_user_stories_summary(self) -> None:
        package = package_fixture()
        package["project"] = "Unit Test Project"
        package["files"] = [{
            "name": "418532862_b4fba7eb7c3d4bc2b7a3f5ba64486280-060726-1812-754.pdf",
            "kind": "pdf",
            "stored_path": "/tmp/418532862_b4fba7eb7c3d4bc2b7a3f5ba64486280-060726-1812-754.pdf",
            "extraction_note": "извлечен текст PDF",
            "masked_text": (
                "1. Описание User Story по MVP2. Документ описывает пользовательские сценарии "
                "для системы автоматизированной обработки заявок. US-07 Направление результата "
                "на расчетчика. US-08 Получение и просмотр задачи на расчет. US-09 Заполнение "
                "формы компенсации. US-10 Валидация и редактирование цен. US-11 Отправка "
                "заполненной формы Эксперту. US-12 Формирование шаблона соглашения. "
                "US-13 Выгрузка шаблона соглашения Word/PDF."
            ),
            "mask_review": {"unresolved_pii": False},
        }]
        package["evidence_plan"] = []

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        source = next(item for item in plan.items if item.destination == "50_Sources")
        self.assertEqual(source.title, "Сценарии MVP2")
        self.assertTrue(source.selected)
        self.assertIn("50_Sources/UTP - Сценарии MVP2.md", source.target_path)
        self.assertIn("US-07..US-13", source.body)
        self.assertIn("TBD", source.body)
        self.assertNotIn("418532862 b4fba7eb7c3d4bc2b7a3f5ba", source.title)

    def test_hash_named_pdf_gets_contextual_mvp2_data_model_summary(self) -> None:
        package = package_fixture()
        package["project"] = "Unit Test Project"
        package["files"] = [{
            "name": "418532873_66e0cb0268e64d60bc1954da62144b02-060726-1812-760.pdf",
            "kind": "pdf",
            "stored_path": "/tmp/418532873_66e0cb0268e64d60bc1954da62144b02-060726-1812-760.pdf",
            "extraction_note": "извлечен текст PDF",
            "masked_text": (
                "4. Описание модели данных MVP2. Модель данных для карточки Заявки. "
                "Поля: ID заявки, Проект, Объект, Квартира / помещение, Статус заявки, "
                "Ответственный, Источник CRM / Ручное / Импорт, связанные файлы, результат ИИ, "
                "статус отправки в Техзор, лог интеграций CRM / AI / Техзор. "
                "Модель данных для фильтров, сортировки, поиска и таблице заявок."
            ),
            "mask_review": {"unresolved_pii": False},
        }]
        package["evidence_plan"] = []

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        source = next(item for item in plan.items if item.destination == "50_Sources")
        self.assertEqual(source.title, "Модель данных")
        self.assertTrue(source.selected)
        self.assertIn("50_Sources/UTP - Модель данных.md", source.target_path)
        self.assertIn("карточки заявки", source.body)
        self.assertIn("Техзор", source.body)
        self.assertNotIn("418532873 66e0cb0268e64d60bc1954da", source.title)

    def test_hash_named_pdf_gets_contextual_mvp2_status_model_summary(self) -> None:
        package = package_fixture()
        package["project"] = "Unit Test Project"
        package["files"] = [{
            "name": "418532871_5902d13e9e8f404f91804b67686d015e-060726-1812-758.pdf",
            "kind": "pdf",
            "stored_path": "/tmp/418532871_5902d13e9e8f404f91804b67686d015e-060726-1812-758.pdf",
            "extraction_note": "извлечен текст PDF",
            "masked_text": (
                "3. Статусная модель MVP2\n"
                "3.1 Статусная модель для MVP2. Статусы Заявки. "
                "Отправлено в расчет, Расчет в процессе, Расчет завершен, "
                "Формирование соглашения, Готово к отправке в Техзор, Отправлено в Техзор. "
                "Статусы негативного сценария: Ошибка расчета цен, Форма неполная, Ошибка формирования соглашения."
            ),
            "mask_review": {"unresolved_pii": False},
        }]
        package["evidence_plan"] = []

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        source = next(item for item in plan.items if item.destination == "50_Sources")
        self.assertEqual(source.title, "Статусная модель")
        self.assertTrue(source.selected)
        self.assertIn("50_Sources/UTP - Статусная модель.md", source.target_path)
        self.assertIn("Отправлено в расчет", source.body)
        self.assertIn("Негативные сценарии", source.body)
        self.assertNotIn("418532871 5902d13e9e8f404f91804b67", source.title)

    def test_hash_named_pdf_uses_content_heading_fallback(self) -> None:
        package = package_fixture()
        package["project"] = "Unit Test Project"
        package["files"] = [{
            "name": "999999999_deadbeefdeadbeefdeadbeef.pdf",
            "kind": "pdf",
            "stored_path": "/tmp/999999999_deadbeefdeadbeefdeadbeef.pdf",
            "extraction_note": "извлечен текст PDF",
            "masked_text": (
                "5. Интеграционный контур\n"
                "Документ описывает обмен данными между внутренними системами, очередь событий и обработку ошибок."
            ),
            "mask_review": {"unresolved_pii": False},
        }]
        package["evidence_plan"] = []

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        source = next(item for item in plan.items if item.destination == "50_Sources")
        self.assertEqual(source.title, "Интеграционный контур")
        self.assertTrue(source.selected)
        self.assertIn("50_Sources/UTP - Интеграционный контур.md", source.target_path)
        self.assertIn("Смысловое имя получено из извлеченного текста", source.body)

    def test_related_launch_sources_are_merged_into_one_source_summary(self) -> None:
        package = package_fixture()
        package["project"] = "Unit Test Project"
        package["files"] = [
            {
                "name": "АПР - Определение дальнейших шагов по реализации Автопретензий.docx",
                "kind": "docx",
                "stored_path": "/tmp/steps.docx",
                "extraction_note": "текст извлечен",
                "masked_text": "Определение дальнейших шагов по реализации Автопретензии: риски, владельцы, сроки.",
                "mask_review": {"unresolved_pii": False},
            },
            {
                "name": "АПР_Определение дальнейших шагов по реализации Автопретензий-20260616_170334-Запись собрания.txt",
                "kind": "text",
                "stored_path": "/tmp/steps-transcript.txt",
                "extraction_note": "transcript готов",
                "masked_text": "Встреча про дальнейшие шаги Автопретензии: запуск, список вопросов и статусы.",
                "mask_review": {"unresolved_pii": False},
            },
        ]
        package["evidence_plan"] = []

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        sources = [item for item in plan.items if item.destination == "50_Sources"]
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].title, "Шаги запуска")
        self.assertEqual(sources[0].operation, "merge")
        self.assertIn("/tmp/steps.docx", sources[0].body)
        self.assertIn("/tmp/steps-transcript.txt", sources[0].body)

    def test_dialog_scribe_plan_extracts_deterministic_architecture_signals(self) -> None:
        package = package_fixture()
        package["files"] = [
            {
                "name": "gdrs_meeting.mp3",
                "kind": "media",
                "stored_path": "/tmp/gdrs_meeting.mp3",
                "extraction_note": "готово: transcript.txt",
                "masked_text": (
                    "Обсуждали СКУД, синхронизатор данных, базу данных и Face ID для отчетности. "
                    "Для разовых пропусков не ясно, как фиксировать подрядчик. "
                    "Проход по кнопке охраны уходит в бумажный журнал. "
                    "Нужно выбрать мастер-систему для названий проектов. "
                    "Старый TelegramBot и битрикс-бот требуют разделения ролей."
                ),
                "mask_review": {"unresolved_pii": False},
            }
        ]
        package["evidence_plan"] = []

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        destinations = {item.destination for item in plan.items}
        self.assertIn("10_Branches", destinations)
        self.assertIn("30_Open_Questions", destinations)
        self.assertIn("40_Risks", destinations)
        self.assertTrue(any("СКУД" in item.body and "Face ID" in item.body for item in plan.items))
        architecture = next(item for item in plan.items if "СКУД" in item.body and "Face ID" in item.body)
        self.assertIn("## Суть", architecture.body)
        self.assertIn("## Контекст", architecture.body)
        self.assertIn("## Как использовать в Gaia", architecture.body)

    def test_existing_target_is_not_selected_for_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects = root / "Проекты"
            project_dir = projects / "Автопретензии"
            (project_dir / "Память_Graph" / "50_Sources").mkdir(parents=True)
            (project_dir / "АПР - Память.md").write_text("# Автопретензии\n", encoding="utf-8")
            settings = SimpleNamespace(
                projects=projects,
                service_docs=root / "Сервис",
                scribe_candidate_classifier=False,
                scribe_classifier_timeout_seconds=1,
            )
            package = package_fixture()
            package["files"] = [{
                "name": "meeting_audio.mp3",
                "kind": "media",
                "stored_path": "/tmp/meeting_audio.mp3",
                "extraction_note": "готово: transcript.txt",
                "masked_text": "Короткий источник встречи.",
                "mask_review": {"unresolved_pii": False},
            }]
            with patch("gaia.scribe.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                first = create_scribe_plan(package)
                source = next(item for item in first.items if item.destination == "50_Sources")
                (project_dir / source.target_path).write_text("already exists", encoding="utf-8")
                second = create_scribe_plan(package)

        source = next(item for item in second.items if item.destination == "50_Sources")
        self.assertFalse(source.selected)
        self.assertEqual(source.status, "existing")
        self.assertEqual(source.operation, "existing_target")

    def test_scribe_apply_can_append_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects = root / "Проекты"
            project_dir = projects / "Автопретензии"
            for folder in ("00_Core", "10_Branches", "20_Decisions", "30_Open_Questions", "40_Risks", "50_Sources", "90_Archive"):
                (project_dir / "Память_Graph" / folder).mkdir(parents=True, exist_ok=True)
            (project_dir / "АПР - Память.md").write_text("# Автопретензии\n", encoding="utf-8")
            (project_dir / "Память_Graph" / "АПР - Индекс памяти.md").write_text("# АПР - Индекс памяти\n\n## Вовлеченные узлы\n\n", encoding="utf-8")
            settings = SimpleNamespace(
                projects=projects,
                service_docs=root / "Сервис",
                scribe_candidate_classifier=False,
                scribe_classifier_timeout_seconds=1,
            )
            package = package_fixture()
            package["files"] = [{
                "name": "status.docx",
                "kind": "docx",
                "stored_path": "/tmp/status.docx",
                "extraction_note": "текст извлечен из Word",
                "masked_text": "Документ описывает статусы дефектов и новые правила переходов.",
                "mask_review": {"unresolved_pii": False},
            }]
            with patch("gaia.scribe.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                plan = create_scribe_plan(package)
                source = next(item for item in plan.items if item.destination == "50_Sources")
                target = project_dir / source.target_path
                target.write_text(
                    "---\ntype: source_summary\nlast_verified_at: 2026-01-01\n---\n\n# Existing\n",
                    encoding="utf-8",
                )
                existing = create_scribe_plan(package)
                item = next(item for item in existing.items if item.destination == "50_Sources")
                result = apply_scribe_plan(package, [item.id], {item.id: "update_existing"})

            updated = target.read_text(encoding="utf-8")
            self.assertEqual(result.applied, [item.id])
            self.assertIn("last_verified_at:", updated)
            self.assertIn("## Дополнение Scribe", updated)
            self.assertIn("Статусы дефектов: source-summary", updated)
            self.assertIn("Scribe plan item", updated)

    def test_scribe_apply_can_create_linked_node_for_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects = root / "Проекты"
            project_dir = projects / "Автопретензии"
            for folder in ("00_Core", "10_Branches", "20_Decisions", "30_Open_Questions", "40_Risks", "50_Sources", "90_Archive"):
                (project_dir / "Память_Graph" / folder).mkdir(parents=True, exist_ok=True)
            (project_dir / "АПР - Память.md").write_text("# Автопретензии\n", encoding="utf-8")
            (project_dir / "Память_Graph" / "АПР - Индекс памяти.md").write_text("# АПР - Индекс памяти\n\n## Вовлеченные узлы\n\n", encoding="utf-8")
            settings = SimpleNamespace(
                projects=projects,
                service_docs=root / "Сервис",
                scribe_candidate_classifier=False,
                scribe_classifier_timeout_seconds=1,
            )
            package = package_fixture()
            package["files"] = [{
                "name": "status.docx",
                "kind": "docx",
                "stored_path": "/tmp/status.docx",
                "extraction_note": "текст извлечен из Word",
                "masked_text": "Документ описывает статусы дефектов и отдельный контекст процесса.",
                "mask_review": {"unresolved_pii": False},
            }]
            with patch("gaia.scribe.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                plan = create_scribe_plan(package)
                source = next(item for item in plan.items if item.destination == "50_Sources")
                (project_dir / source.target_path).write_text("already exists", encoding="utf-8")
                existing = create_scribe_plan(package)
                item = next(item for item in existing.items if item.destination == "50_Sources")
                result = apply_scribe_plan(package, [item.id], {item.id: "create_linked"})

            self.assertEqual(result.applied, [item.id])
            linked = [Path(path) for path in result.changed_files if "50_Sources" in path and path.endswith(".md")]
            self.assertTrue(any("дополнение" in path.name for path in linked))
            self.assertTrue(any("Дополняет [[" in path.read_text(encoding="utf-8") for path in linked))

    def test_scribe_apply_can_skip_existing_target_as_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects = root / "Проекты"
            project_dir = projects / "Автопретензии"
            for folder in ("00_Core", "10_Branches", "20_Decisions", "30_Open_Questions", "40_Risks", "50_Sources", "90_Archive"):
                (project_dir / "Память_Graph" / folder).mkdir(parents=True, exist_ok=True)
            (project_dir / "АПР - Память.md").write_text("# Автопретензии\n", encoding="utf-8")
            settings = SimpleNamespace(
                projects=projects,
                service_docs=root / "Сервис",
                scribe_candidate_classifier=False,
                scribe_classifier_timeout_seconds=1,
            )
            package = package_fixture()
            package["files"] = [{
                "name": "status.docx",
                "kind": "docx",
                "stored_path": "/tmp/status.docx",
                "extraction_note": "текст извлечен из Word",
                "masked_text": "Документ описывает статусы дефектов.",
                "mask_review": {"unresolved_pii": False},
            }]
            with patch("gaia.scribe.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                plan = create_scribe_plan(package)
                source = next(item for item in plan.items if item.destination == "50_Sources")
                target = project_dir / source.target_path
                target.write_text("already exists", encoding="utf-8")
                existing = create_scribe_plan(package)
                item = next(item for item in existing.items if item.destination == "50_Sources")
                result = apply_scribe_plan(package, [item.id], {item.id: "skip_duplicate"})

            self.assertEqual(result.applied, [])
            self.assertEqual(result.skipped, [item.id])
            self.assertEqual(target.read_text(encoding="utf-8"), "already exists")

    def test_scribe_apply_writes_selected_graph_node_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects = root / "Проекты"
            service_docs = root / "Сервис"
            project_dir = projects / "Автопретензии"
            (project_dir / "Память_Graph" / "20_Decisions").mkdir(parents=True)
            for folder in ("00_Core", "10_Branches", "30_Open_Questions", "40_Risks", "50_Sources", "90_Archive"):
                (project_dir / "Память_Graph" / folder).mkdir(parents=True, exist_ok=True)
            (project_dir / "АПР - Память.md").write_text("# Автопретензии\n", encoding="utf-8")
            (project_dir / "Память_Graph" / "АПР - Индекс памяти.md").write_text("# АПР - Индекс памяти\n\n## Вовлеченные узлы\n\n", encoding="utf-8")
            settings = SimpleNamespace(
                projects=projects,
                service_docs=service_docs,
                scribe_candidate_classifier=True,
                scribe_classifier_timeout_seconds=1,
            )
            package = package_fixture()
            with (
                patch("gaia.scribe.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
                patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value={
                    "decisions": ["Принято решение использовать staged review перед записью памяти."],
                    "rules": [],
                    "risks": [],
                    "open_questions": [],
                    "technical_facts": [],
                    "exclude": [],
                }),
            ):
                plan = create_scribe_plan(package)
                selected = [item.id for item in plan.items if item.destination == "20_Decisions"]
                result = apply_scribe_plan(package, selected)

            self.assertEqual(len(result.applied), 1)
            self.assertTrue(Path(result.backup_path).exists())
            self.assertTrue(any("20_Decisions" in path for path in result.changed_files))
            self.assertTrue((project_dir / "АПР - Источники.md").exists())
            self.assertIn("Scribe apply", (project_dir / "АПР - Журнал памяти.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
