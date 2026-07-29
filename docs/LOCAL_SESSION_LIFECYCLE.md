# Локальная сессия Gaia

Backend создаёт новый runtime session token после каждого запуска. Открытый WebView может хранить прежнюю HttpOnly cookie, поэтому mutation сначала получает `403 mutation_not_authorized` до бизнес-handler.

При загрузке UI запрашивает `POST /api/session/refresh`. Endpoint доступен только loopback-запросу с точным Gaia Origin, `Content-Type: application/json` и заголовком `X-Gaia-Session-Refresh: 1`; он принимает только пустой JSON, устанавливает новую HttpOnly SameSite=Strict Path cookie и возвращает только `status: ready` и безопасный `runtime_id`.

Все mutation запросы проходят общий wrapper. Только доказанный `403 mutation_not_authorized` вызывает один refresh и один повтор исходного запроса. Другие ответы, сеть, отмена и повторный 403 не повторяются. Cookie/token, материалы и URL с идентификаторами не попадают в diagnostics. WebKit cache, website data, receipts, jobs и временные файлы не очищаются.

Ручная проверка: оставьте Gaia.app открытым, перезапустите backend и нажмите mutation. Первый rejection должен автоматически обновить сессию; context compilation должна получить одну `202` job без ручной перезагрузки WebView.
