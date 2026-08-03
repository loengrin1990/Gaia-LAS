---
name: gaia-close-stable-state
description: "Используй для явного полного закрытия существенного опубликованного slice Gaia: сверки одного stable SHA в Git, GitNexus и MemoryHub вместе с безопасным snapshot."
---

# Gaia: закрытие stable state

Используй только после публикации существенного slice или при прямом поручении закрыть stable state. Исключение для микроскопического изменения не выбирай самостоятельно: пропустить трёхслойную синхронизацию можно лишь по явному указанию задачи.

Существенная работа окончательно закрыта только при согласованности четырёх слоёв: Git `main`/`origin/main` содержит принятую реализацию, документация Gaia согласована с принятой реализацией и contracts, GitNexus index относится к тому же stable SHA, а MemoryHub canonical state вместе с безопасным snapshot фиксирует тот же SHA. Git остаётся источником фактической реализации; normative contracts — источником обязательных правил; GitNexus — графом кода; MemoryHub — слоем состояния проекта.

## Workflow

1. Определи stable SHA текущего `main`.
2. Подтверди совпадение с `origin/main`.
3. Проверь clean working tree.
4. Подтверди через documentation-governance, что документационный state согласован с принятой реализацией и contracts; не устраняй `CONTRACT_VIOLATION` переписыванием normative документа.
5. Обнови или перестрой GitNexus index штатной командой проекта и проверь, что он относится к тому же SHA.
6. Обнови MemoryHub canonical state через его утверждённый snapshot-only workflow.
7. Создай или обнови безопасный snapshot согласно privacy/storage contract: в него не входят загрузки, storage, резервные копии, базы, рабочие данные или секреты.
8. Подтверди, что MemoryHub canonical state и snapshot фиксируют тот же stable SHA.
9. Выполни предусмотренные project doctor/consistency checks.
10. Верни reconciliation report с Git stable SHA, `origin/main` SHA, documentation state, GitNexus SHA/state, MemoryHub canonical-state SHA, snapshot SHA/reference, результатами checks и явным признаком наличия либо отсутствия рассинхронизации.

## Граница завершения

Если хотя бы один слой не синхронизирован, stable-state closure не завершён. Не исправляй несоответствия молча и не меняй архитектурные contracts: сообщи конкретный несинхронизированный слой и безопасный следующий шаг.
