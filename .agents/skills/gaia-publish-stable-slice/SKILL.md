---
name: gaia-publish-stable-slice
description: "Используй только при явном поручении интегрировать и опубликовать уже одобренный feature slice Gaia в stable state безопасным fast-forward способом."
---

# Gaia: публикация одобренного stable slice

Запускай этот навык только по явному поручению на integration/publication. GitNexus и MemoryHub synchronization в него не входят; для полного закрытия используй отдельный `gaia-close-stable-state`.

## Workflow

1. Явно определи source branch и target branch.
2. Проверь чистоту working tree, ожидаемые SHA `HEAD` и релевантную ancestry.
3. Убедись, что source — именно одобренное состояние, а не просто технически готовая ветка.
4. Подтверди, что выполнены required targeted/full validation и documentation-governance `sync`; незакрытый `CONTRACT_VIOLATION` допускается только при явном разрешении публикации несмотря на него. Не запускай тяжёлый `full-refresh` автоматически.
5. Выполни требуемые pre-merge проверки.
6. Если это предусмотрено порученным workflow, создай архив прежнего stable `main`.
7. Интегрируй только безопасным fast-forward способом.
8. Если fast-forward невозможен, остановись и сообщи причину; не выбирай другую стратегию интеграции.
9. Выполни targeted и full checks уже на итоговом `main`.
10. Выполни обычный push.
11. Подтверди, что `main` и `origin/main` указывают на ожидаемый stable SHA.
12. Верни точный отчёт: source/target, исходные и итоговые SHA, ancestry, documentation sync, проверки с results/counts, push и final branch state.

## Жёсткие запреты

Никогда не используй force push, `rebase`, `cherry-pick`, `squash`, `commit --amend` или попытки «починить историю». Не выполняй stable-state synchronization автоматически после публикации.
