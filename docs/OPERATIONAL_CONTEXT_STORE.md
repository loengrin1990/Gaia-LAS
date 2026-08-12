# Operational Context Store v0

Этот документ описывает реализацию OC-1. Нормативная семантика остаётся в [контракте Operational Context v0](OPERATIONAL_CONTEXT_V0.md); данный документ не расширяет её и не подключает OC к runtime.

## Граница и хранение

Реализация находится в `gaia.operational_context`. Она не использует legacy `ProvenanceStore` и не читает его `context/current` records. Общими остаются только проверенные технические примитивы атомарной записи и локальной блокировки.

Данные OC хранятся отдельно, в partition для точной пары `scope + scope_ref`. Имя partition — SHA-256 этой пары, а не raw reference. Поэтому API чтения требует точные `scope`, `scope_ref` и `item_id`; он не поддерживает wildcard, поиск по имени, last-used fallback или cross-project enumeration. Каждая controlled write меняет один partition одной атомарной заменой файла.

## Модель и начальный registry

`OperationalItem` содержит весь обязательный набор OC-0: opaque `id`, scope, scope_ref, kind, subject_ref, value/reference, structured provenance, timestamps, lifecycle, confirmed confirmation_ref, sensitivity и optional supersedes_id. Identity — точная четвёрка `scope + scope_ref + kind + subject_ref`; значение в неё не входит.

Начальный закрытый registry переиспользует совместимую vocabulary Stage 7:

| Kind | Допустимые scopes | Subject identity | Composition / precedence |
|---|---|---|---|
| `requirement` | project, system | opaque requirement id | отсутствует |
| `decision` | project, system | opaque decision/constraint id | отсутствует |
| `risk` | project, system | opaque risk id | отсутствует |
| `open_question` | project | opaque question id | отсутствует |
| `action` | user, project | opaque action/commitment id | отсутствует |

Отсутствие правила означает, что OC-1 не выбирает победителя и не вводит общую лестницу scope.

`SafeProvenance` принимает только `source_ref`, `candidate_ref` и optional `memory_ref`, все как opaque references. В ней нет полей для source content, quote, file path или свободного metadata. `ConfirmationEvidence` также содержит только action, target/prior item relation, opaque actor_ref, timestamp и candidate/source reference. При promotion/replacement store требует точного совпадения каждого candidate/source reference evidence с provenance item: подтверждение от постороннего candidate или source отклоняется.

## Controlled operations

`create()` создаёт active confirmed item исключительно вместе с immutable promotion evidence. `replace()` в одной записи partition одновременно создаёт новый item, evidence, `supersedes_id` и переводит ровно один прежний active item той же identity в `superseded`. Любая ошибка до записи оставляет старый item active. `retire()` допускает только `active → retired` и записывает immutable retirement evidence в той же атомарной операции.

Прямого update semantic fields нет. New semantic revision получает новый id. `lineage()` проходит связь new → old из `supersedes_id` и old → new по локальному partition index.

Перед каждым чтением store принимает только schema version 1 и заново валидирует все persisted items/evidence через те же модели, включая точное соответствие partition scope и scope_ref, confirmation action/target и provenance. Повреждённый, неизвестный или несогласованный JSON отклоняется безопасной ошибкой.

## Граница OC-2

OC-1 не выполняет retrieval, ranking, conflict resolution или privacy eligibility выдачи. Его точные scope partitions — подготовленная storage boundary для OC-2: будущий reader сможет открыть только запрошенные exact user/project/system partitions, а затем применять eligibility к уже изолированному набору. Legacy `current` не становится `active`, миграция не запускается, Context Assembler, Dialogue и Heart не меняются.
