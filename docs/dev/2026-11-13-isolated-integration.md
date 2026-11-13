# Gaia — изолированная Qwen/feature integration

Дата: 13.11.2026. Authority: historical verification. Только интеграционная feature-версия, не production rollout/stable/MVP. Отдельная ветка `codex/gaia-isolated-integration`, worktree `tmp/isolated-integration`.

## Основание и Git

Прямое координационное поручение разрешило обычное объединение **в новой integration-ветке**, сохранив рабочий checkout и все соседние работы. Источники merge: первый parent `1f1e43340a48a4e64f4c3588b92dee00b8820fe6` (сохранённый Qwen-профиль), второй parent `3d1530dc60942e8370ffa144df5169e5aff3fa83` (проверенный stack и bounded audit). Общая база — `0667f94a52f16b26239d87fe71a90dd547f51bd8`. Обычный `--no-ff --no-commit` остановлен до проверки/commit; история не переписывалась. Это разрешение не включает merge в main, изменение рабочего runtime/config или deployment.

Входит весь stack из [исходного audit](2026-11-13-integration-audit.md), Qwen-коммит и проверенный перенос **из snapshot**, не из меняющихся файлов. Основной `/Users/ilia/Documents/Gaia` остаётся на `codex/local-model-profile-qwen9b`, HEAD1f1e433, с прежним dirty-состоянием. Quote worktree остаётся на d30b695. Local main0667f94 и опубликованный main1e3b95a не переключались этой операцией.

## Backup и перенос

До операций создан отдельный приватный резерв вне Git: 7 tracked dirty-файлов и 20 untracked-файлов main, 5 файлов quote worktree; **32 файла** проверены SHA-256. Сохранены binary diff, полные файлы, index и исходные HEAD/branch/status. Реальные config не копировались: только отпечатки для проверки сохранности. Backup/manifest/raw logs не публикуются. Точное место и manifest checksum сохранены в локальном handoff.

Перенесены tracked README, USER_INTERFACE, HTML и четыре тестовых файла. `tests/test_conversations.py` и `tests/test_scribe.py` уже совпадали с feature — повторная правка не потребовалась. Дополнительно перенесён **просмотренный релевантный** `tests/test_project_status_ui.js`: он проверяет внесённые пользователем подписи и не меняет приложение. Остальные untracked не добавлялись.

| Конфликт | Разрешение |
|---|---|
| docs/USER_INTERFACE.md | Сохранены оба раздела: подготовленный контекст и пользовательское описание статусов проекта |
| tests/test_context_compiler.py | Набор assertions буквально одинаков после нормализации пробелов, отличается порядок `preload/unload`; все проверки сохранены |
| tests/test_local_status.py | Явная независимая фикстура со snapshot Qwen9B/16k для project-health, все assertions Qwen-коммита сохранены; production ради тестов не менялся |

README сохраняет Qwen-инструкции и исходные локальные заметки. `config.example.json`, `config.review.example.json` и Obsidian profile README **byte-identical 1f1e433**. Это не смена всех маршрутов на Qwen и не квалификация живой модели. В `gaia/**` относительно 3d1530d изменён только HTML: пользовательские названия статуса/структуры, отсутствие ложного healthy-state для unknown и отделение структуры от готовности модели.

## Проверенная ценность и границы

Основной UI объединяет статусы проектов, поиск очищенных материалов, различение accepted/pending, подготовку прежнего Dialogue-пакета без модели и чтение того же очищенного основания. Privacy/source/version/confirmation gates, формат Assembler и contracts не менялись при разрешении конфликтов. Tristan остаётся отдельно запускаемым synthetic-инструментом; main provider/автоматический внешний маршрут не подключались. Addressed остаётся выключенным по умолчанию.

Финальные targeted: local-status18, Compiler56, изменённые UI32 — pass. Полные **Python676/Node64**, smoke1 — pass; 0 failures/errors/skips/cancelled. Четыре добавившихся Node-теста — сохранённый project-status UI test. В начале переноса два тестовых модуля получили SyntaxError из-за ещё не удалённых conflict markers после сбоя whitespace-контроля; это исправлено до финальных targeted/full проверок, без изменения production или ослабления assertions.

Браузер на временном профиле с отключёнными моделями: новые подписи в Projects/Dialogue; поиск2материалов; очистка/повтор; accepted1 и pending1 различаются; пакет содержит только accepted1 и показывает полный источник. Console errors0. Cancel, поздние ответы, повторные решения и удержание/освобождение compute slot покрыты детерминированными full suites; физическая отмена живой модели этим UI-прогоном не проверялась. Это инженерная симуляция, не независимое пользовательское исследование. Полная semantic completeness, real-data/model qualification и rollout не подтверждены.

Veil и `.github`, recovery doc, отдельный quote slice не включены и не удалены. Все 32 исходных файла, main/quote HEAD/branch/index/status и config-отпечатки повторно сверены. Реальные данные, память, runtime8796, VPN, model processes и старые budget guards не менялись. Model/paid calls0. Собственный temporary UI остановлен, профиль удалён.

## Как опробовать

Из этой integration-копии: `/Users/ilia/Documents/Gaia/.venv/bin/python -B -m scripts.smoke_core_journey --serve`. Единственный canonical сценарий и ограничения — [E2E runbook](../E2E_VALIDATION.md#повторяемая-проверка-основного-пути-без-модели). Он создаёт собственный профиль и печатает собственный URL; обычный launcher и рабочий storage не нужны. Ctrl+C закрывает пробу.

Следующий шаг — личная проба интегрированной версии и review итогового SHA. Переключение рабочего запуска, использование реальных профилей/памяти и rollout требуют отдельного разрешения. Наличие этой merge feature не переносит stable label и не включает соседние работы автоматически.
