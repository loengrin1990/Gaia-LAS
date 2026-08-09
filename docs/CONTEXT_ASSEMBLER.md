# Context Assembler

`gaia.context_assembler` — внутренняя read-only граница для будущего Dialogue. Она отбирает только query-scoped, `confirmed` и `current` Operational Context внутри workspace, затем структурированно компонует его с существующим Lore `MemorySelection`.

Граница не формирует prompt, не вызывает модель и не меняет Context, Lore, Scribe, storage или Dialogue runtime. Operational Context остаётся слоем `CURRENT AUTHORITY` с идентификатором и native provenance; Lore остаётся отдельным слоем `KNOWLEDGE / HISTORY / RATIONALE`.

`DialogueContextBudget` задаёт общий символьный бюджет и резерв для Operational Context. Неиспользованная ёмкость одного слоя доступна другому; элементы Context не обрезаются, чтобы не терять связь с provenance.

Контракт: MemoryHub `DEC-20260809-005-context-assembler-composition-contract`.

Dialogue передаёт полный contextual query в Lore. Для trusted Context selection он строит отдельную bounded projection: термины текущего сообщения идут первыми, а последний пользовательский вопрос добавляется только как компактный предмет для follow-up. Это сохраняет ограничение Context Search и не сокращает Lore query. Только Dialogue создаёт read-only reader уже существующего workspace; если workspace ещё не зарегистрирован или в нём нет подходящего Context, это нормальное пустое состояние. Другие вызовы `create_package`, включая Scribe и rebuild, не передают этот reader и сохраняют прежний Lore-only prompt.

В Dialogue prompt слои остаются отдельными: «Текущий операционный контекст» помечен как источник истины для текущего состояния, а «Память проекта, выбранная Lore» — как знания, история и обоснование. При расхождении Context имеет приоритет только для трактовки текущего состояния; автоматического объединения, подавления или изменения записей нет.

Пустой trusted Context не является ошибкой и не мешает Dialogue. Но повреждённая запись, прошедшая базовый trust-filter, не скрывается переходом на Memory-only: composition завершается ошибкой, чтобы не выдать неполный package за доверенный результат.
