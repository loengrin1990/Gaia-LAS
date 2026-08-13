# Context Assembler

`gaia.context_assembler` — внутренняя read-only граница для Dialogue. Текущая legacy-реализация отбирает только query-scoped, `confirmed` и `current` Context внутри workspace, затем структурированно компонует его с существующим Lore `MemorySelection`.

Термин `current` в существующей реализации — pre-v0/legacy semantics. Он не считается автоматически эквивалентным `lifecycle=active` из Operational Context v0: mapping/adaptation и доказательство его scope, subject identity и confirmation evidence относятся к отдельному будущему slice.

Граница не формирует prompt, не вызывает модель и не меняет Context, Lore, Scribe, storage или Dialogue runtime. Operational Context остаётся слоем `CURRENT AUTHORITY` с идентификатором и native provenance; Lore остаётся отдельным слоем `KNOWLEDGE / HISTORY / RATIONALE`.

`DialogueContextBudget` задаёт общий символьный бюджет и резерв для Operational Context. Неиспользованная ёмкость одного слоя доступна другому; элементы Context не обрезаются, чтобы не терять связь с provenance.

Эта реализация описывает уже существующую workspace-ограниченную legacy-границу и не является Operational Context v0. Нормативные scope, lifecycle, privacy, promotion и conflict semantics определены в [Operational Context v0](OPERATIONAL_CONTEXT_V0.md). Отдельный OC-3 v0 path реализован в `gaia.operational_context_assembler`: он принимает готовый результат OC-2, Lore-selected Memory и session input, но не изменяет эту legacy-реализацию или её runtime callers. Его границы описаны в [Operational Context Assembler v0](OPERATIONAL_CONTEXT_ASSEMBLER.md). Контракт legacy-композиции: MemoryHub `DEC-20260809-005-context-assembler-composition-contract`.

Dialogue передаёт полный contextual query в Lore. Для trusted Context selection он строит отдельную bounded projection: термины текущего сообщения идут первыми, а последний пользовательский вопрос добавляется только как компактный предмет для follow-up. Это сохраняет ограничение Context Search и не сокращает Lore query. Только Dialogue создаёт read-only reader уже существующего workspace; если workspace ещё не зарегистрирован или в нём нет подходящего Context, это нормальное пустое состояние. Другие вызовы `create_package`, включая Scribe и rebuild, не передают этот reader и сохраняют прежний Lore-only prompt.

В Dialogue prompt слои остаются отдельными: «Текущий операционный контекст» помечен как источник истины для текущего состояния, а «Память проекта, выбранная Lore» — как знания, история и обоснование. При расхождении Context имеет приоритет только для трактовки текущего состояния; автоматического объединения, подавления или изменения записей нет.

Пустой trusted Context не является ошибкой и не мешает Dialogue. Но повреждённая запись, прошедшая базовый trust-filter, не скрывается переходом на Memory-only: composition завершается ошибкой, чтобы не выдать неполный package за доверенный результат.
