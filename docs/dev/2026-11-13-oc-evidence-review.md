# Gaia — очищенное основание из OC-карточки

Дата: 13.11.2026. Authority: historical verification. Baseline6308a25402375a470fc5ea0abcb45a5f1bee4326, новая изолированная ветка `codex/oc-evidence-review`, worktree `tmp/oc-evidence-review`. Прямо утверждённый локальный review slice, не rollout/stable/MVP.

## Frozen scope и результат

Человек открывает конкретный связанный очищенный материал прямо из pending или принятой current project-карточки. Утверждение модели/принятый факт отделено от основания. SafeProvenance OC v0 сохраняет source_ref, но не offsets/quote: поэтому первая версия честно показывает **полный материал**, без выдуманной точной цитаты и без восстановления фрагмента из похожего текста. Это уменьшает переходы по интерфейсу; смысловую достоверность модели и полноту доказательств не гарантирует.

Новый точечный GET в существующем review API разрешает ID карточки по exact scope, проверяет состояние OC и читает ту же sanitized source version через существующую readonly_review. Подтверждение, current lineage, checksum очищенного текста и source/extraction integrity обязательны. Read-only checksum проверка не использует мутирующий verify_source. No-store response не содержит raw bytes/path/source IDs. Окно не запускает модель, не пишет память, не подтверждает OC и не выдаёт права внешнего раскрытия. Persistence/schema/ADR не меняются. [Canonical contract](../OPERATIONAL_CONTEXT_REVIEW_UX.md#локальное-очищенное-основание).

Неподдержанные основания: candidate-only/Memory references, raw-source/extraction pointers, historical/unresolved records и raw PDF images/точная страница. Устаревшая/отозванная/неподтверждённая/изменённая цепочка получает отказ без подмены текущим похожим источником. Наличие уже принятого OC не позволяет восстановить недоступный source. Состояние проверяется при каждом открытии; открытое окно не отслеживает дальнейшие отзывы автоматически.

## Проверки приложения и человеческого пути

8 Python-тестов с настоящими intake, parser/OC bridge и Handler на disposable store: pending→отдельный HTTP confirm→current; повторные чтения byte-identical по всему store; отсутствие автоматического принятия; wrong/missing workspace и собственная pending/current карточка с чужим source pointer; altered sanitized/extraction/raw checksums; withdrawal/source supersession/new clean version; missing files/unconfirmed review; candidate-only/raw/Memory refs; retired item. Модели в HTTP фазе запрещены. Для compiler setup воспроизводится неизменённый сохранённый синтетический ответ, без live calls.

8 JS-проверок реального viewer/card кода: pending/current endpoints, явные подписи material/statement/неизвестной точности, textContent вместо HTML, один GET при double click в полёте, close/Escape/abort, late result после close/reopen/project change, повторный close-event, отсутствие старого текста при отказе/невалидной форме ответа. Поздние ответы проверены детерминированно на этом уровне; browser network latency искусственно не менялась.

В основном браузерном UI временного synthetic-профиля выполнены: pending evidence→исходный абзац; Escape→всё ещё pending; отдельное явное принятие учебного предложения→current evidence с тем же материалом; отзыв только disposable source→понятный отказ и очищенное окно; второй проект→пустая OC-очередь без чужих источников. Это инженерная симуляция review, не независимый пользовательский тест. Полный raw upload/picker и live model не повторялись. Собственный UI остановлен, временный профиль удалён.

В существующем smoke добавлен opt-in `--serve --oc-evidence`, чтобы повторить этот пользовательский путь без JSON/терминальных IDs, модели или рабочих файлов. Он использует существующие frozen source/statement как явную display fixture; не является ещё одной model qualification. [Личная проба на 2–3 минуты](../E2E_VALIDATION.md#личная-проба-основания-oc-без-модели).

## Impact и гигиена

До правок — проверенный backup вне Git и clean6308a25. GitNexus: OC card HIGH9 impacted, handler GET LOW1 direct/1 flow, workspace change LOW с динамической UI-связью проверенной вручную, HTML loader LOW5 impacted, smoke main LOW1. HIGH предупреждение сообщено до изменения. Широкие renderer/showScreen с CRITICAL impact исследованы, но не изменены; новый viewer подключён без изменения их поведения. Post-edit exact rendered inline projection5 scripts и обязательный detect_changes проверены. Staged CRITICAL12files/74symbols/16flows, compare-main CRITICAL82files/899symbols/72flows; предупреждение сообщено до commit. Graph включает соседний OC POST как changed, но AST comparison с6308a25 подтверждает: все остальные Handler methods, включая POST, AST-структурно неизменны. Реальные изменения ограничены GET routing, новым read-only handler, viewer/asset wiring и opt-in smoke. Широкая регрессия покрывает существующие write/cancel/repeat flows; это не разрешение на merge/rollout.

Реальные данные/память/config, рабочий dirty checkout, соседние ветки/quote/Veil, 8796/VPN не менялись. Модельных/внешних платных вызовов0; Tristan budget/guards не затронуты. Main merge/deploy не выполняются. Следующий шаг — личный review на синтетике; поддержка точной цитаты для будущих OC-предложений требует отдельного выбора persistence/provenance контракта, не подразумевается этой вехой.

Targeted8Python/8Node прошли; final full688Python/76Node, failures0/errors0/skips0/cancelled0; core smoke1 pass и публичная --serve --oc-evidence проба подтверждены через настоящий GET без изменения очереди. Python syntax,5 rendered JS syntax, config sanity, documentation-governance sync/check33 и diff checks прошли. Сохранность HEAD/branch/status/index/config и32dirty/untracked файлов main/quote подтверждена; backup6 затронутых существующих файлов вне Git проверен. CI и remote SHA сообщаются отдельно после публикации.
