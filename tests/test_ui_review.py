from __future__ import annotations

import unittest

from gaia.ui import INDEX_HTML


class UiReviewContractTests(unittest.TestCase):
    def test_review_controls_exist(self) -> None:
        self.assertIn('id="reviewPanel"', INDEX_HTML)
        self.assertIn('id="promptPreview"', INDEX_HTML)
        self.assertIn('id="reviewConfirm"', INDEX_HTML)
        self.assertIn('id="reviewCopyBtn"', INDEX_HTML)
        self.assertIn('id="scribeBtn"', INDEX_HTML)
        self.assertIn('id="profile"', INDEX_HTML)
        self.assertIn('onchange="updateCopyState()"', INDEX_HTML)

    def test_copy_requires_review_confirmation(self) -> None:
        self.assertIn("function updateCopyState()", INDEX_HTML)
        self.assertIn("checkbox.checked", INDEX_HTML)
        self.assertIn("lastPackage.local_fallback_required", INDEX_HTML)
        self.assertIn("lastPackage.safe_for_codex_after_confirmation", INDEX_HTML)
        self.assertIn("reviewCopyBtn.disabled = !canCopy", INDEX_HTML)
        self.assertIn("Копирование заблокировано", INDEX_HTML)
        self.assertIn("function copyTextToClipboard(text)", INDEX_HTML)
        self.assertIn("clipboard timeout", INDEX_HTML)
        self.assertIn("document.execCommand('copy')", INDEX_HTML)
        self.assertIn("Браузер запретил доступ к буферу обмена.", INDEX_HTML)

    def test_scribe_draft_is_manual_action(self) -> None:
        self.assertIn("function createScribePlan()", INDEX_HTML)
        self.assertIn("function applyScribePlan()", INDEX_HTML)
        self.assertIn("/api/scribe-plan", INDEX_HTML)
        self.assertIn("/api/scribe-apply", INDEX_HTML)
        self.assertIn("function createScribeDraft()", INDEX_HTML)
        self.assertIn("/api/scribe-draft", INDEX_HTML)
        self.assertIn("Gaia сохраняет черновик обновления памяти без записи", INDEX_HTML)
        self.assertIn('id="tab-scribe"', INDEX_HTML)
        self.assertIn('id="panel-scribe"', INDEX_HTML)
        self.assertIn('id="scribePlanList"', INDEX_HTML)
        self.assertIn("scribe-plan-checkbox", INDEX_HTML)
        self.assertIn("function updateScribeState(data)", INDEX_HTML)
        self.assertIn("function scribeBlockedReason(data)", INDEX_HTML)
        self.assertIn("const canCreateScribeDraft = !hasUnresolvedPii(data)", INDEX_HTML)
        self.assertIn("!!data.local_fallback_required || !canCreateScribeDraft", INDEX_HTML)
        self.assertIn("Обновление памяти заблокировано: есть неподтвержденный риск ПД.", INDEX_HTML)
        self.assertIn("Обновление памяти заблокировано: контекст требует локальной обработки.", INDEX_HTML)
        self.assertIn("Проектная память не менялась автоматически.", INDEX_HTML)
        self.assertIn("Разобрать выбранный файл", INDEX_HTML)
        self.assertIn("Предложить записи в память", INDEX_HTML)
        self.assertIn("Записать выбранное в память", INDEX_HTML)

    def test_profile_selection_is_sent_to_analyze(self) -> None:
        self.assertIn("function loadProfiles()", INDEX_HTML)
        self.assertIn("/api/profiles", INDEX_HTML)
        self.assertIn("form.append('profile'", INDEX_HTML)
        self.assertIn("profile_title", INDEX_HTML)
        self.assertIn('id="profileDescription"', INDEX_HTML)
        self.assertIn("profilesById[profile.id] = profile", INDEX_HTML)
        self.assertIn("updateProfileDescription();", INDEX_HTML)
        self.assertIn("Профиль задачи изменен. Пересобери контекст перед локальным ответом.", INDEX_HTML)
        self.assertIn("function updateProfileDescription()", INDEX_HTML)

    def test_lore_sources_are_rendered(self) -> None:
        self.assertIn("function renderMemorySources(data)", INDEX_HTML)
        self.assertIn("function renderEvidencePlan(root, items)", INDEX_HTML)
        self.assertIn('id="loreDetails"', INDEX_HTML)
        self.assertIn('id="loreSourceList"', INDEX_HTML)
        self.assertIn('id="rebuildPromptBtn"', INDEX_HTML)
        self.assertIn("function rebuildPromptWithSelectedLore()", INDEX_HTML)
        self.assertIn("/api/rebuild-prompt", INDEX_HTML)
        self.assertIn("lore-source-checkbox", INDEX_HTML)
        self.assertIn("memory_sources", INDEX_HTML)
        self.assertIn("memory_total_sections", INDEX_HTML)
        self.assertIn("evidence_plan", INDEX_HTML)
        self.assertIn("совпадения", INDEX_HTML)

    def test_local_fallback_disables_confirmation(self) -> None:
        self.assertIn("checkbox.disabled = !!data.local_fallback_required", INDEX_HTML)
        self.assertIn('state.textContent = "локально"', INDEX_HTML)

    def test_local_answer_has_preflight_timeout_and_cancel(self) -> None:
        self.assertIn('id="localStatus"', INDEX_HTML)
        self.assertIn('id="cancelLocalBtn"', INDEX_HTML)
        self.assertIn("function checkLocalStatus()", INDEX_HTML)
        self.assertIn("/api/local-status", INDEX_HTML)
        self.assertIn("function cancelLocalAnswer()", INDEX_HTML)
        self.assertIn("function showLocalCanceled()", INDEX_HTML)
        self.assertIn("localAnswerCanceled", INDEX_HTML)
        self.assertIn("localAnswerTimedOut", INDEX_HTML)
        self.assertIn("AbortController", INDEX_HTML)
        self.assertIn("LOCAL_ANSWER_TIMEOUT_MS", INDEX_HTML)
        self.assertIn("showLocalHealthCheckTimedOut", INDEX_HTML)
        self.assertIn("showLocalAnswerTimedOut", INDEX_HTML)
        self.assertIn("status.status === 'timeout'", INDEX_HTML)
        self.assertLess(INDEX_HTML.index("const status = await checkLocalStatus()"), INDEX_HTML.index("fetch('/api/local-answer'"))
        self.assertIn("Локальный запрос отменен. Backend мог продолжить ожидание LM Studio.", INDEX_HTML)
        self.assertIn("LM Studio не успела ответить на короткий health-check.", INDEX_HTML)
        self.assertIn("Локальный ответ не завершился за таймаут интерфейса.", INDEX_HTML)
        self.assertIn("Это не означает, что LM Studio выключена", INDEX_HTML)
        self.assertIn("LM Studio не отвечает. Запусти LM Studio или используй внешний маршрут после проверки данных.", INDEX_HTML)

    def test_result_summary_is_primary(self) -> None:
        self.assertIn('id="processingSummary"', INDEX_HTML)
        self.assertIn("Итог обработки", INDEX_HTML)
        self.assertIn('id="summaryState"', INDEX_HTML)
        self.assertIn('id="summaryExternal"', INDEX_HTML)
        self.assertIn('id="summaryNext"', INDEX_HTML)
        self.assertIn("Внешний маршрут доступен, но не активирован.", INDEX_HTML)
        self.assertIn("Внешний маршрут заблокирован", INDEX_HTML)
        self.assertLess(INDEX_HTML.index('id="processingSummary"'), INDEX_HTML.index('id="output"'))
        self.assertIn("Следующий шаг", INDEX_HTML)
        self.assertIn("Локальный ответ получен.", INDEX_HTML)
        self.assertIn("Копирование заблокировано.", INDEX_HTML)
        self.assertIn("Запрос скопирован.", INDEX_HTML)

    def test_job_result_tabs_exist_with_json_last(self) -> None:
        self.assertIn('id="tab-summary"', INDEX_HTML)
        self.assertIn('id="tab-security"', INDEX_HTML)
        self.assertIn('id="tab-lore"', INDEX_HTML)
        self.assertIn('id="tab-scribe"', INDEX_HTML)
        self.assertIn('id="tab-prompt"', INDEX_HTML)
        self.assertIn('id="tab-json"', INDEX_HTML)
        self.assertIn("function showTab(name)", INDEX_HTML)
        self.assertLess(INDEX_HTML.index('id="panel-prompt"'), INDEX_HTML.index('id="panel-json"'))
        self.assertLess(INDEX_HTML.index('id="panel-json"'), INDEX_HTML.index('id="output"'))

    def test_api_errors_are_readable_and_json_stays_technical(self) -> None:
        self.assertIn("function renderReadableError(message, payload)", INDEX_HTML)
        self.assertIn('id="summaryReason"', INDEX_HTML)
        self.assertIn("Ошибка обработки", INDEX_HTML)
        self.assertIn("setOutput(JSON.stringify(payload", INDEX_HTML)

    def test_empty_request_is_validated_before_analyze_post(self) -> None:
        self.assertIn('id="analysisWarning"', INDEX_HTML)
        self.assertIn("Добавь запрос или файл для анализа.", INDEX_HTML)
        self.assertIn("function showAnalysisWarning(message, neutral)", INDEX_HTML)
        self.assertIn("function clearAnalysisWarning()", INDEX_HTML)
        self.assertIn("if (!query && !hasFiles)", INDEX_HTML)
        self.assertLess(INDEX_HTML.index("if (!query && !hasFiles)"), INDEX_HTML.index("fetch('/api/analyze'"))

    def test_file_without_query_is_allowed_with_warning(self) -> None:
        self.assertIn("if (!query && hasFiles)", INDEX_HTML)
        self.assertIn("Запрос не указан: анализ будет строиться по файлу и профилю.", INDEX_HTML)
        self.assertIn("form.append('query', queryInput.value)", INDEX_HTML)

    def test_form_changes_invalidate_prepared_prompt(self) -> None:
        self.assertIn("function invalidatePreparedPackage(message)", INDEX_HTML)
        self.assertIn("function bindFormDirtyHandlers()", INDEX_HTML)
        self.assertIn("filesInput.addEventListener('change'", INDEX_HTML)
        self.assertIn("queryInput.addEventListener('input'", INDEX_HTML)
        self.assertIn("Список файлов изменен. Пересобери контекст перед локальным ответом.", INDEX_HTML)
        self.assertIn("lastPrompt = \"\"", INDEX_HTML)
        self.assertIn("document.getElementById('localBtn').disabled = true", INDEX_HTML)
        self.assertIn("bindFormDirtyHandlers();", INDEX_HTML)

    def test_context_ready_card_opens_compact_external_review(self) -> None:
        self.assertIn("Review и копировать", INDEX_HTML)
        self.assertIn("function openExternalReview()", INDEX_HTML)
        self.assertIn("showInspectorTab('prompt')", INDEX_HTML)
        self.assertIn("panel.classList.add('attention')", INDEX_HTML)
        self.assertIn("Контекст проверен, исходные персональные данные не видны, внешний анализ разрешаю.", INDEX_HTML)
        self.assertIn("Скопировать запрос", INDEX_HTML)

    def test_veil_review_table_is_safe_and_actionable(self) -> None:
        self.assertIn("function veilReviewTable(rows)", INDEX_HTML)
        self.assertIn("function safeTokens(review)", INDEX_HTML)
        self.assertIn("function veilAction(review, data)", INDEX_HTML)
        self.assertIn("Есть неподтвержденный риск ПД", INDEX_HTML)
        self.assertIn("Нужна ручная проверка", INDEX_HTML)
        self.assertIn("Требуется локально", INDEX_HTML)
        self.assertIn("Разрешено", INDEX_HTML)
        self.assertIn("finding.token", INDEX_HTML)
        self.assertNotIn("finding.sample", INDEX_HTML)

    def test_technical_json_redacts_review_samples_in_ui(self) -> None:
        self.assertIn("function redactTechnicalPayload(value)", INDEX_HTML)
        self.assertIn("key === 'sample'", INDEX_HTML)
        self.assertIn("[скрыто в UI]", INDEX_HTML)
        self.assertIn("redactTechnicalPayload(data)", INDEX_HTML)

    def test_dialog_mode_and_collapsible_inspector_exist(self) -> None:
        self.assertIn('class="context-panel"', INDEX_HTML)
        self.assertIn('class="dialog-panel"', INDEX_HTML)
        self.assertIn('id="dialogTimeline"', INDEX_HTML)
        self.assertIn('id="inspectorPanel"', INDEX_HTML)
        self.assertIn('id="inspectorToggle"', INDEX_HTML)
        self.assertIn("function toggleInspector()", INDEX_HTML)
        self.assertIn("inspector-collapsed", INDEX_HTML)
        self.assertIn("function addDialogCard(kind, title, content, actions)", INDEX_HTML)
        self.assertIn("Контекст готов", INDEX_HTML)
        self.assertIn("Локальный ответ", INDEX_HTML)
        self.assertIn("Диалог не выбран. Gaia создаёт новый диалог", INDEX_HTML)
        self.assertIn("conversation-row:hover", INDEX_HTML)
        self.assertLess(INDEX_HTML.index('id="dialogTimeline"'), INDEX_HTML.index('id="output"'))

    def test_agent_testing_ux_regressions_are_covered(self) -> None:
        self.assertIn("status-chip", INDEX_HTML)
        self.assertIn("Индикатор слоя Lore, не кнопка", INDEX_HTML)
        self.assertIn("chip status-chip ok", INDEX_HTML)
        self.assertIn("status-chip::before", INDEX_HTML)
        self.assertIn('id="validateProjectBtn"', INDEX_HTML)
        self.assertIn("Проверяю структуру проекта", INDEX_HTML)
        self.assertIn("button.disabled = true", INDEX_HTML)
        self.assertIn("prompt был сокращен для LM Studio", INDEX_HTML)
        self.assertIn("data.local_result && !data.local_result.ok", INDEX_HTML)
        self.assertIn("function evidenceItemLabel(item)", INDEX_HTML)
        self.assertIn("Подтверждено ${confirmed} из ${items.length}", INDEX_HTML)
        self.assertIn("route.title = data.route", INDEX_HTML)
        self.assertIn("function loreCoverageLabel(data)", INDEX_HTML)
        self.assertIn("подтвержденный контекст по запросу не найден", INDEX_HTML)
        self.assertIn("Уточни запрос терминами проекта", INDEX_HTML)
        self.assertIn("function safeTokenExplanation(data)", INDEX_HTML)
        self.assertIn("function safeTokenExamples(data)", INDEX_HTML)
        self.assertIn("Безопасные токены вида ${examples}", INDEX_HTML)
        self.assertIn("'[INN_1]'", INDEX_HTML)
        self.assertIn("function tokenNote(data)", INDEX_HTML)
        self.assertIn("addDialogCard('gaia', 'Безопасные токены'", INDEX_HTML)
        self.assertIn("Lore не нашёл подходящих разделов — анализ источников не выполнялся.", INDEX_HTML)

    def test_conversation_local_answer_updates_safety_summary(self) -> None:
        self.assertIn("function renderConversationLocalSummary(data)", INDEX_HTML)
        self.assertIn("if (runLocal) renderConversationLocalSummary(data);", INDEX_HTML)
        self.assertIn("Локальный ответ получен в продолжении диалога.", INDEX_HTML)
        self.assertIn("Внешний маршрут не использовался.", INDEX_HTML)
        self.assertIn("Veil: ${packageData.query_mask_status || '-'}, замен ${packageData.query_mask_replacements || 0}.", INDEX_HTML)
        self.assertIn("Проектная память не менялась автоматически.", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
