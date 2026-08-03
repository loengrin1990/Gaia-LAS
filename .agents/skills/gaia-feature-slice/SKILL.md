---
name: gaia-feature-slice
description: "Используй для стандартной реализации ограниченного feature slice в Gaia: от preflight и чтения действующих contracts до проверок, документации и структурированного отчёта."
---

# Gaia: реализация feature slice

Используй этот навык только для явно порученной реализации в Gaia. Он описывает рабочий процесс Codex, а не архитектуру системы: актуальные architecture/ADR и contracts всегда читаются в canonical документации перед изменением соответствующей подсистемы.

## Workflow

1. Проверь baseline: branch, `HEAD`, clean/dirty working tree и релевантную ancestry. Сохрани исходные значения для отчёта.
2. Прочитай source-of-truth документацию по затронутой подсистеме, включая действующие privacy, storage, provenance, workspace-isolation, export и human-confirmation contracts.
3. Сформулируй минимальный scope и границы того, что остаётся вне задачи. При изменении символов выполни GitNexus impact analysis согласно `AGENTS.md`.
4. Исследуй существующую реализацию и тесты; не подменяй принятое архитектурное решение собственным.
5. Внеси минимальные изменения, нужные для задачи. Не добавляй попутный рефакторинг.
6. Выполни целевые тесты.
7. Выполни релевантную полную регрессию; для обычной Python-задачи Gaia canonical команда — `python3 -B -m unittest discover -s tests`.
8. Выполни применимые syntax/static checks (в том числе `python3 -B -m gaia.config`, когда затронута конфигурация или нужен стандартный sanity check) и `git diff --check`.
9. Проверь `git diff` на scope creep. Перед commit выполни GitNexus `detect_changes()` согласно `AGENTS.md`.
10. Обнови существующую canonical документацию, если изменён contract, workflow или поведение.
11. Повтори проверки, на которые повлияли документация или финальные правки.
12. Создавай commit только если это прямо разрешено текущей задачей.
13. Верни структурированный итог: branch, исходный/итоговый SHA, файлы, scope, проверки с counts и всеми failures/errors/skips, документация, status и вне-scope findings.

## Границы

Не выполняй merge, публикацию `main`, stable-state synchronization или архитектурные решения. Не расширяй scope. Если blocker нельзя безопасно снять в рамках принятого решения, верни его как blocker с вариантами для архитектора.
