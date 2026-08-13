# Operational Context Assembler v0

Этот документ описывает реализацию OC-3. Нормативные правила остаются в [Operational Context v0](OPERATIONAL_CONTEXT_V0.md); OC-1 хранит state, а OC-2 определяет trusted-local current authority.

OC-3 отвечает на вопрос: «Как сохранить уже выбранные локальные authority, Memory и минимальный session context в одном bounded package?» Он реализован отдельным read-only модулем `gaia.operational_context_assembler`. Это не legacy `gaia.context_assembler`, не prompt renderer и не интеграция с Dialogue или Heart.

Входом служат query/task с trusted upstream handling, готовый typed результат OC-2, уже выбранный Lore `MemorySelection` с handling и явно переданные минимальные session units с handling. Assembler не классифицирует raw text эвристиками: classification обязан передать владелец соответствующей upstream boundary. Модуль не открывает OC partitions, не запускает Lore search, не читает legacy Context, не вызывает Scribe и не меняет storage, lifecycle, candidate или Memory.

Package хранит отдельными полями current OC authority, OC ambiguities, Lore/Memory и session context. OC ambiguity не превращается в current fact и не получает winner. Расхождение OC и Memory не reconciled: оба слоя остаются в package вместе со своими native provenance/authority metadata.

Размер package ограничен общим budget. Query/task, каждый authority item, каждая ambiguity, вся Lore selection и каждый session unit считаются отдельными atomic units. Невместившаяся единица детерминированно исключается с `budget_exceeded` и её handling; включённый authority item или ambiguity никогда не обрезается от identity, provenance, confirmation или sensitivity. Пустой OC или Memory — нормальный результат.

Handling package — наиболее строгий handling всех материально рассмотренных inputs: query, task, OC authority, OC ambiguity, Lore selection и session context. Поэтому restricted input сохраняет package `restricted` даже при budget omission его content; budget уменьшает content, но не classification. Automatic declassification отсутствует. OC-3 не выполняет routing, cloud/local permissions или downstream disclosure; эти действия остаются границей OC-5.
