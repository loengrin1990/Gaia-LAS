# Operational Context Review / UX v0

OC-4 добавляет отдельный локальный review слой `gaia.operational_context_review` поверх OC-1 Store. Он не читает и не мигрирует уже сохранённые legacy `ContextService`/`context/current`; прежний экран остаётся доступным отдельно.

Для новых material-derived candidates действует forward-only bridge: если existing extraction дал closed-registry `kind`, а exact cleaned evidence содержит поддерживаемую явную метку предмета, кандидат создаётся только в OC review queue с provenance на подтверждённый очищенный материал и детерминированный candidate reference. В v0 к таким явным меткам относятся «текущий статус поставки» и «финальное согласование». Legacy-карточка для того же кандидата не создаётся. Без поддерживаемой явной метки предмета кандидат остаётся в legacy-потоке: bridge не выводит identity из заголовка или текста модели. При уже active OC того же kind/subject новое предложение становится replacement и всё равно требует отдельного подтверждения человека. Если для того же project subject уже есть другое pending OC-предложение, новый кандидат безопасно пропускается: project-scope conflict не создаётся обходом replacement semantics и не дублируется в legacy. Повтор одного и того же extraction идемпотентен. Bridge не выполняет массовую миграцию, не меняет сохранённые legacy records и не подтверждает/promote candidate автоматически. При отсутствии положительно подтверждённого STANDARD handling material-derived candidate получает `unknown` и остаётся только в локальной обработке.

## Экран

В существующем разделе «Контекст проекта» появились две области Operational Context:

- «Требует решения» показывает только pending v0 candidates: предложенное текущее состояние, понятный тип, контекст, безопасное описание источника, handling «Обычный» или «Ограниченный» и причину. В ней доступны «Подтвердить» и «Отклонить»; для replacement — «Заменить» и «Оставить как есть» рядом с «Было / Стало».
- «Текущий контекст» показывает только `active + confirmed` OC v0 выбранного project scope. Pending, rejected, legacy, superseded и retired записи в него не попадают. Для active item доступно «Больше не актуально» с явным подтверждением.
- На той же странице находятся сворачиваемые области «Нерешённые противоречия» и «История проверки». Первая сохраняет отложенные противоречия доступными для повторного ручного решения; вторая показывает закрытые предложения и historical OC-1 states понятными полями: содержание, статус, безопасный источник и handling. Это не новая навигация и не legacy Summary.

Restricted значение видно только в локальном интерфейсе Gaia. Карточка не выводит internal IDs, partition names, raw source refs, provenance JSON или diagnostics.

## Controlled actions

`OperationalContextReviewService.confirm()` строит OC-1 promotion либо replacement evidence строго из provenance выбранного candidate. Replacement-карточка до действия показывает «Было» и «Стало». Если `replaces_id` указан, вызывается атомарный `OperationalContextStore.replace()`: до human confirmation старый active item остаётся единственным current; после него старый становится `superseded`, а новый active появляется одной операцией. `reject()` меняет только candidate state и не создаёт runtime-visible item. `retire()` вызывает OC-1 retirement, не удаляя historical record. Каждый successful action возвращает обновлённые «Требует решения» и «Текущий контекст»; интерфейс применяет этот view сразу, без reload.

Typed OC-2 ambiguity текущего project/local-system окружения показывается как «Нужно уточнение» с human-readable kind, каждым безопасно представленным вариантом, его локальным подтверждённым источником и handling. Подсказка предлагает отметить неактуальный вариант. У каждой альтернативы есть controlled OC-1 retirement «Больше не актуально»: retired item сразу исключается из current authority, а противоречие исчезает, когда остаётся один применимый факт. Экран не выбирает winner автоматически.

«Оставить нерешённым» фиксирует defer этого review decision: карточка уходит из active «Требует решения», но underlying ambiguity не меняется и остаётся discoverable в «Нерешённых противоречиях». Новый или изменённый ambiguity получает новый review reference и снова может стать actionable. Confirm немедленно переносит факт в «Текущий контекст»; reject остаётся в «Истории проверки» как отклонённый; replacement оставляет новый факт current, а старый — в истории как заменённый; retirement оставляет retired факт в истории. Во всех случаях ответ action API содержит обновлённый view, который UI применяет без reload.

## Boundary

OC-4 не выполняет extraction, semantic privacy classification, downstream disclosure/routing, Memory migration, legacy Context migration или изменения Dialogue/Heart. Кнопка «Открыть прежнюю сводку» явно ведёт только в legacy Project Summary и не обещает показать OC v0. Candidate queue минимальна и изолирована, потому что legacy Stage-7 candidate records не доказывают обязательные OC v0 scope, subject identity, sensitivity и immutable confirmation provenance.
