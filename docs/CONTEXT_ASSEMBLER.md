# Context Assembler

`gaia.context_assembler` — внутренняя read-only граница для будущего Dialogue. Она отбирает только query-scoped, `confirmed` и `current` Operational Context внутри workspace, затем структурированно компонует его с существующим Lore `MemorySelection`.

Граница не формирует prompt, не вызывает модель и не меняет Context, Lore, Scribe, storage или Dialogue runtime. Operational Context остаётся слоем `CURRENT AUTHORITY` с идентификатором и native provenance; Lore остаётся отдельным слоем `KNOWLEDGE / HISTORY / RATIONALE`.

`DialogueContextBudget` задаёт общий символьный бюджет и резерв для Operational Context. Неиспользованная ёмкость одного слоя доступна другому; элементы Context не обрезаются, чтобы не терять связь с provenance.

Контракт: MemoryHub `DEC-20260809-005-context-assembler-composition-contract`.
