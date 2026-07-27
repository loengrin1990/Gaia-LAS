# Контракт нативного host Gaia

## Граница ответственности

`Gaia.app` создаёт одно окно AppKit с `WKWebView`. Он находит и при необходимости запускает уже существующий Python backend Gaia, а затем открывает его существующий интерфейс. Host не читает DOM, рабочие материалы, тела запросов или содержимое выбранных файлов; не создаёт новый API и не изменяет upload-flow.

## Фактический launcher-контракт

- `app.py` вызывает `gaia.server.main(open_window=True)` и backend слушает loopback host/port из локального `config.json`.
- `GET /api/runtime` возвращает `ready`, непустой `runtime_id` и `api_contract_version`; этого недостаточно заменить просто открытым TCP-портом.
- Старый `launch_gaia_window` ждёт этот endpoint, после чего запускает JXA через `osascript`. JXA оставлен только диагностическим fallback и не изменяется.
- Для native host добавлен узкий `python app.py --no-window`: тот же backend запускается без JXA-окна. Порт аргументом не передаётся: это сохраняет существующий конфигурационный контракт без широкого рефакторинга.

## Поиск и жизненный цикл

В исходной реализации 6.3 процесс входил в AppKit event loop, но `@main AppDelegate` не создавал явный `NSApplication`, не назначал delegate и не удерживал его сильной ссылкой. Поэтому callback не запускал окно и backend: физический запуск оставлял невидимый процесс без дочернего Python-процесса. В 6.3a `main.swift` создаёт `NSApplication`, удерживает `AppDelegate` локальной сильной ссылкой на всё время `application.run()`, назначает его delegate и активирует regular application policy. В `applicationDidFinishLaunching` сначала создаётся единственный `NSWindow` с текстом «Gaia запускается…», затем запускается coordinator в фоне; после readiness тот же window получает `WKWebView`.

Приоритет backend: `GAIA_BACKEND_URL`, затем `config.json` найденного репозитория. Приоритет репозитория: `GAIA_REPOSITORY_ROOT`, затем предсказуемое родительское расположение dev-сборки. Python выбирается в порядке `GAIA_PYTHON`, `.venv/bin/python3`, `/usr/bin/python3`; shell не используется.

Host сначала проверяет точный loopback `/api/runtime`. Валидный ответ означает attached backend и никогда не завершается host. Если Gaia не подтверждена, host запускает owned backend через `Foundation.Process` с массивом аргументов и ждёт readiness максимум 8 секунд вне main thread. При закрытии вызывает `terminate()` только owned process и ждёт не более трёх секунд; рабочие данные не затрагиваются.

`zsh scripts/smoke_macos_host.sh` собирает app, запускает его с безопасной диагностикой и синтетическим loopback backend. Smoke прошёл: подтверждены вход в native lifecycle, `applicationDidFinishLaunching`, создание loading window, старт coordinator и валидное attached-соединение; после него не остаётся owned backend.

## WebKit и файлы

Навигация разрешена только для `http://127.0.0.1:<фактический-порт>`; внешний URL, другой port, `file:`, `data:` и `javascript:` блокируются. `WKUIDelegate.webView(_:runOpenPanelWith:initiatedByFrame:completionHandler:)` переносит `allowsMultipleSelection` и `allowsDirectories` в `NSOpenPanel`. Отмена передаёт `nil`, подтверждение — исходный `[URL]`; локальный guard вызывает completion только один раз. URL не преобразуются в строки и не сохраняются.

HTML inputs остаются прежними: оба принимают `multiple`, подпись `label/for` исправлена, а drag-and-drop и системный выбор далее сходятся в существующем JavaScript upload-flow и backend API.

### Accepted-path 6.3b

Физическая проверка подтвердила, что cancel работает, panel открывается повторно, а accepted-путь не создавал материал. Аудит показал первую точку разрыва: `journeyFiles` не имел обработчика `input` или `change`; единственный вызов `uploadJourneyMaterials()` был привязан к отдельной кнопке. Поэтому даже доставленный WebKit `FileList` не передавался в существующий upload-flow.

Исправление — один `change`-обработчик актуального input, который вызывает уже существующий `uploadJourneyMaterials()`. Он читает `input.files` до очистки `input.value`; очистка по-прежнему происходит только после успешного ответа `/api/analyze`. Новый endpoint, native upload или передача путей не добавлены. Host фиксирует только безопасные счётчики accepted-path и получает от страницы безопасные события click/input/change/upload через диагностический message handler.

Debug target не включает App Sandbox и не содержит entitlement-файла, поэтому sandbox и security-scoped access не были причиной и не менялись.
