---
name: documentation-governance
description: "Управление документацией Gaia: классификация authority, targeted sync после feature slice и глубокий full-refresh только по прямому поручению или на крупной stable boundary."
---

# Gaia: управление документацией

Используй этот навык для документации внутри Gaia. Он не заменяет architecture contracts, Git, GitNexus или MemoryHub и не управляет их жизненным циклом.

Ключевой принцип: жанр документа и его authority независимы. Один устойчивый факт имеет один canonical home; краткая ссылка допустима, полное дублирование — нет.

## Режимы

### `write`

Перед созданием или существенным изменением документа определи genre и authority, найди canonical home, проверь отсутствие уже существующего места для этого знания и прочитай authoritative sources. Не создавай конкурирующий источник истины.

### `sync`

Это обычный дешёвый targeted режим после feature slice:

1. Определи scope из задачи и `git diff`.
2. Найди затронутые contracts, config, runtime и документы через [карту документов](references/document-map.json).
3. При необходимости используй GitNexus `impact` или changed flows для narrowing, затем прочитай реальные релевантные файлы.
4. Проверь направление implementation → docs и docs → implementation/config.
5. Обнови только descriptive или operational документ, если есть доказанный drift. Для code-internal изменений зафиксируй `documentation impact: none`.
6. Не исправляй соседние находки «заодно»; верни их как future audit candidates.

GitNexus помогает сузить область проверки, но не является единственным доказательством.

### `full-refresh`

Запускай только по прямому поручению, на major stable boundary, при подозрении на накопившийся documentation drift или перед явно требуемым крупным архитектурным переходом. Проверь docs → code/config/runtime и code/config/runtime → docs, используя Git, GitNexus и историю только для planning/narrowing. Не превращай аудит в stylistic rewrite.

## Authority и конфликт

Смотри [нормативные правила](references/normative-rules.md) и [порядок верификации](references/verification.md). Если implementation/config/runtime противоречит `normative` документу, результат — `CONTRACT_VIOLATION`. Не переписывай contract под код и верни вопрос для явного архитектурного решения.

`historical` материалы не переписываются под сегодняшний runtime. `operational` handoff обновляется вместе с проектным состоянием, но не переопределяет normative документы.

## Границы Gaia stable state

Documentation governance работает только с документацией репозитория Gaia. Он не пишет в MemoryHub, не создаёт decisions автоматически и не закрывает stable state самостоятельно. При closure роли остаются раздельными: Git — реализация, normative contracts — обязательные правила, GitNexus — структурный граф, MemoryHub — canonical project state и snapshot. См. [handoff и stable state](references/handoff-state.md).

## Проверка skill

Перед использованием после изменения структуры запусти:

```bash
python3 .agents/skills/documentation-governance/scripts/check_docs.py
```

См. также [reference](references/reference.md), [runbook](references/runbook.md), [decision analysis](references/decision-analysis.md) и [инструкции агенту](references/agent-instruction.md).
