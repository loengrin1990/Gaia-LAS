# Operational Context Runtime Integration v0

Этот документ описывает фактическую интеграцию OC-5. Нормативными источниками остаются [Operational Context v0](OPERATIONAL_CONTEXT_V0.md) и [PB-0](PRIVACY_BOUNDARY_INPUT_HANDLING.md); данный документ не меняет их правила.

## Поток

При локальном запуске Project Dialogue при наличии существующего workspace выполняется OC-2 retrieval с точными local user/project/system identity и policy `standard + restricted`. OC-3 сохраняет retrieved authority, явные ambiguity, Lore Memory и минимальные недавние turns в typed package. Новое содержательное сообщение и legacy Memory получают handling `unknown`; session наследует этот же handling.

OC-5 принимает только готовый typed package. Он не выполняет retrieval, не выбирает сторону в ambiguity и не мигрирует старые данные. Если у legacy caller нет OC workspace, сохраняется прежний локальный путь.

Для обычного ответа OC-5 отключает JSON mode только для своего локального вызова и требует от модели естественный русский текст. Если пользователь явно запросил JSON, этот формат разрешён и не преобразуется normalizer-ом. Распознанный internal envelope с `status: missing` или явным `reasoning_error`/parser/schema failure не попадает в Dialogue как JSON; сходство ключей само по себе не делает пользовательский JSON внутренним. Для пустого или ошибочного internal envelope OC-5 делает одну bounded local recovery attempt с тем же prepared package; внешнего fallback нет. Если восстановление не удалось, Gaia показывает понятное русское сообщение. Если envelope ложно отрицает наличие подтверждённого `delivery_status`, OC-5 возвращает точное значение уже включённой current authority; это не создаёт нового факта и не заменяет retrieval. При ambiguity package содержит локально разрешённые human-readable alternatives, а runtime требует от модели объяснить противоречие без выбора победителя.

## Локальная модель и envelope

Маршрут `operational_context_dialogue` использует локальный Ollama provider `ollama_qwen3_5_9b` с моделью `qwen3.5:9b`, context length 16 384 и response reserve 900. Это normal maximum, а не обязательная цель заполнения.

Перед вызовом provider OC-5 сериализует весь package и вычисляет консервативный безопасный размер по UTF-8. Если полный package вместе с response reserve не входит в 16k, provider не вызывается. Gaia возвращает понятное сообщение о необходимости сузить вопрос или обработать материал по этапам; authority, ambiguity, handling и provenance не отбрасываются ради частичного ответа.

## Routing и privacy

PB-0 остаётся единственным источником external eligibility. При `local_processing_required` OC-5 вызывает только local route и никогда не передаёт package внешнему executor. Это включает ordinary free-form query, restricted OC, legacy Memory без classification и session, зависящий от них. Недоступность local provider не создаёт fallback во внешний контур.

Если все required inputs runtime-attested `standard`, OC-5 открывает только controlled external-eligible seam. В v0 реальный внешний provider не добавляется: seam может использовать test double. Explicit ambiguity standard inputs передаётся как ambiguity, а не превращается в current truth.

## Наблюдаемость

Результат runtime содержит только безопасные категории: выбранный route, модель, оценку размера package, факт oversize rejection и итог provider call. Raw package, restricted content и hidden conflict details в telemetry не записываются.
