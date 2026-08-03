---
name: gaia-close-stable-state
description: "Используй для явного полного закрытия существенного опубликованного slice Gaia: сверки одного stable SHA в Git, GitNexus и MemoryHub вместе с безопасным snapshot."
---

# Gaia: закрытие stable state

Используй только после публикации существенного slice или при прямом поручении закрыть stable state. Исключение для микроскопического изменения не выбирай самостоятельно: пропустить трёхслойную синхронизацию можно лишь по явному указанию задачи.

Существенная работа окончательно закрыта только при совпадении одного stable SHA в трёх слоях: Git `main`/`origin/main`, актуальный GitNexus index и MemoryHub canonical state вместе с безопасным snapshot.

## Workflow

1. Определи stable SHA текущего `main`.
2. Подтверди совпадение с `origin/main`.
3. Проверь clean working tree.
4. Обнови или перестрой GitNexus index штатной командой проекта и проверь, что он относится к тому же SHA.
5. Обнови MemoryHub canonical state через его утверждённый snapshot-only workflow.
6. Создай или обнови безопасный snapshot согласно privacy/storage contract: в него не входят загрузки, storage, резервные копии, базы, рабочие данные или секреты.
7. Подтверди, что MemoryHub canonical state и snapshot фиксируют тот же stable SHA.
8. Выполни предусмотренные project doctor/consistency checks.
9. Верни reconciliation report с Git stable SHA, `origin/main` SHA, GitNexus SHA/state, MemoryHub canonical-state SHA, snapshot SHA/reference, результатами checks и явным признаком наличия либо отсутствия рассинхронизации.

## Граница завершения

Если хотя бы один слой не синхронизирован, stable-state closure не завершён. Не исправляй несоответствия молча и не меняй архитектурные contracts: сообщи конкретный несинхронизированный слой и безопасный следующий шаг.
