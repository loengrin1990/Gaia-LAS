# Этап 6.1: безопасная runtime-диагностика

## Назначение и границы

Этот временный режим нужен для установления фактов по двум блокирующим дефектам этапа 6: локальной проверке Veil и системному выбору файла в отдельном окне macOS. Он не меняет правила очистки, схему ответа модели, проверку координат, единый HTML upload-flow или drag-and-drop.

Режим выключен по умолчанию. При выключении его поведение и интерфейс Gaia не меняются.

## Включение

Создайте пустой временный журнал и запустите Gaia в том же процессе:

```bash
stage6_diag_path=$(mktemp /private/tmp/gaia-stage6.XXXXXX)
GAIA_STAGE6_RUNTIME_DIAGNOSTICS=1 \
GAIA_STAGE6_DIAGNOSTICS_PATH="$stage6_diag_path" \
python3 -B app.py
```

Журнал состоит из JSONL-событий. Его путь не должен вести в рабочее хранилище Gaia и после исследования может быть удалён как временный файл.

Каждое событие содержит только `timestamp`, `component`, `event_code`, случайный `correlation_id` и небольшой разрешённый набор технических полей. В журнал принципиально не попадают исходный или очищаемый текст, ФИО, prompt, сырой ответ модели, находки, координаты с текстом, содержимое файла, имя файла и путь к файлу.

## Veil: события и интерпретация

События компонента `veil`:

| Событие | Смысл |
| --- | --- |
| `local_model_started` | Начат вызов локальной модели. |
| `local_model_response_received` / `local_model_technical_error` | Ответ получен либо произошла техническая ошибка. |
| `payload_received` | Схема верхнего уровня принята; записаны только `findings_empty` и `findings_count`. |
| `payload_validation_started` | Началась строгая проверка payload. |
| `payload_validation_succeeded` / `payload_validation_failed` | Проверка завершена; при ошибке сохраняется только стабильный код. |

Компонент `review_service` отдельно фиксирует повторную проверку payload и `review_final_state`. Поэтому `ready_for_confirmation` доказан только если последним событием ReviewService является `review_final_state` с этим значением. Само пустое значение `findings_count=0` не является доказательством отсутствия чувствительных данных.

Стабильные коды проверки: `payload_schema_invalid`, `payload_coordinates_invalid`, `payload_validation_invalid`. Ошибки транспорта локальной модели остаются уже существующими безопасными кодами `local_model_*`.

### Контролируемое воспроизведение

Используйте только синтетический текст:

```text
Контактное лицо: Иванов Иван Иванович
```

Запустите проверку этого текста через изолированное временное workspace. В результате сопоставьте порядок событий Veil и ReviewService; не печатайте и не переносите raw payload в документ или постоянный журнал.

### Подтверждённый результат 27.07.2026

На локальном маршруте `veil_review` модель вернула непустой payload: `findings_count=1`. Строгая проверка началась и отклонила находку кодом `payload_coordinates_invalid`. Затем `ReviewService.start()` завершился `review_error` с `local_model_invalid_findings` и нулём принятых находок. Повторная проверка payload в ReviewService не выполнялась: `LocalReviewError` был выброшен раньше, внутри `local_model_review()`.

Следовательно, в этом контролируемом запуске гипотеза о `findings=[]` не подтвердилась. Решение о `ready_for_confirmation` находится в `ReviewService.start()` после успешной повторной валидации, в ветке «нет принятых находок»; эта ветка в данном запуске не достигалась.

## File picker: события и интерпретация

### Исправление аварийного завершения 6.1a

Первый вариант диагностики аварийно завершал отдельное окно сразу после DOM-клика с безопасно классифицируемой причиной `diagnostic_message_handler_selector_unavailable`. Скрипт отправлял сообщение, а WebKit находил зарегистрированный объект, но Objective-C runtime не видел обязательный selector `userContentController:didReceiveScriptMessage:`. Причина: JXA-класс описывал методы массивом, а JXA экспортирует Objective-C selectors только из словаря `methods`, где ключом служит полный selector. Кроме того, для этого callback была указана неверная сигнатура: после скрытых `self` и `_cmd` WebKit передаёт два объекта, `WKUserContentController` и `WKScriptMessage`. Попытка объявить `WKScriptMessageHandler` в `protocols` отдельно проверена и отвергнута самим JXA runtime (`protocol does not exist`): metadata этого WebKit protocol в данном окружении не экспонируется для `registerSubclass`.

`GaiaRuntimeDiagnosticsDelegate` теперь реализует форму `WKScriptMessageHandler` через корректно экспортированный selector `userContentController:didReceiveScriptMessage:` с двумя аргументами и удерживается глобальной ссылкой `runtimeDiagnosticsDelegate` весь жизненный цикл `WKWebView`. Runtime smoke создаёт delegate и подтверждает `respondsToSelector` для точного selector. Обработчик принимает только имя зарегистрированного технического канала, не читает и не записывает сырой `message.body`, а неизвестное сообщение получает безопасный event code.

Этот ремонт не меняет исходный file picker, `WKUIDelegate`, `NSOpenPanel`, drag-and-drop или upload-flow.

В диагностическом режиме WebKit получает временный script message handler только для клика по `input[type=file]`. События компонента `file_picker` образуют последовательность:

1. `dom_file_input_click` — DOM-клик зарегистрирован;
2. `webkit_file_picker_request` — WebKit вызвал `WKUIDelegate`;
3. `open_panel_created`, затем `open_panel_started` — создана и запущена `NSOpenPanel`;
4. `open_panel_finished` — `accepted` или `cancelled`;
5. `completion_handler_called` — completion вызван с `selected_url_count`;
6. `webkit_upload_flow_received` — WebKit получил выбор и должен продолжить прежний upload-flow.

Отсутствие события `webkit_file_picker_request` после `dom_file_input_click` отличается от отмены пользователем: при отмене должны присутствовать события панели и completion handler с `selected_url_count=0`.

### Ручная runtime-проверка

1. Запустите Gaia с флагами выше и откройте отдельное окно Gaia.
2. Нажмите системную кнопку выбора файла, не перетаскивая файл. Сначала убедитесь, что окно не завершилось, а JSONL содержит `dom_file_input_click`.
3. В открывшейся панели нажмите «Отменить». Это проверяет callback, панель и пустой completion без передачи файла.
4. Повторите сценарий с одним временным синтетическим `.txt` с нейтральным автоматически созданным именем. После выбора дождитесь начала штатной обработки в существующем интерфейсе.
5. Сверьте порядок JSONL-событий. Не добавляйте в отчёт путь, имя либо содержание файла.
6. Отдельно перетащите такой же временный файл в прежнюю область загрузки и убедитесь, что он запускает тот же штатный upload-flow; диагностический журнал не должен содержать идентификаторов файла.

### Восстановление сквозного канала 6.1b

Commit `c2ed742` устранил аварийное завершение: delegate действительно экспортирует обязательный selector. Но это ещё не доказывает доставку сообщения. При реальном запуске после одного клика заранее созданный JSONL оставался пустым. Причина установлена: JXA воспринимает нулевые методы `NSFileHandle` как свойства. Вызов `seekToEndOfFile()` выбрасывал исключение, а прежний writer молча его проглатывал. Поэтому ни один событийный слой не был записан.

Теперь Python записывает только собственное событие `diagnostics_python_enabled`, после чего явно передаёт в фактический `osascript` исходные opt-in переменные и безопасный идентификатор их конфигурации. Это не новый общий механизм настроек: дочерний процесс получает ровно окружение запуска. Полный путь не попадает ни в JSONL, ни в stderr.

JXA создаёт отсутствующий файл в существующей родительской папке, дописывает одну JSONL-строку, синхронизирует её до возврата и выдаёт в stderr только фиксированный код события. Отсутствующий путь, отсутствующая родительская папка и ошибка записи имеют отдельные безопасные коды. Другой путь и резервный журнал не используются.

В активный `WKWebViewConfiguration` до загрузки страницы добавляются один `WKUserContentController`, удерживаемый delegate и handler `gaiaStage6Diagnostics`. Временный script страницы не читает страницу, URL, DOM-текст, имя файла или сырое событие. Он только проверяет наличие этого handler и отправляет два фиксированных сообщения: `page_bridge_available` и `page_bridge_ready`. Delegate принимает исключительно разрешённый `event_code`; неизвестный body отклоняется без сериализации.

#### Startup-heartbeat

| Событие | Подтверждённый слой |
| --- | --- |
| `diagnostics_python_enabled` | Python увидел флаг и путь и записал в заданный журнал. |
| `diagnostics_window_process_enabled` | Фактический JXA-процесс унаследовал ту же диагностическую конфигурацию. |
| `diagnostics_handler_registered` | Delegate удерживается, а handler зарегистрирован на controller активной configuration. |
| `diagnostics_page_bridge_available` | Загруженная страница увидела ожидаемый bridge и сообщение дошло до delegate. |
| `diagnostics_page_message_received` | Delegate получил разрешённое стартовое сообщение. |
| `diagnostics_page_ready_recorded` | Это сообщение прошло allowlist и было синхронно записано в JSONL. |

Отсутствующее последнее событие точно показывает последний подтверждённый слой. Python не имитирует события JXA или страницы.

#### Подтверждённый запуск 27.07.2026

Для заранее созданного `/private/tmp/gaia-stage6-manual.jsonl` без пользовательского клика получены все шесть heartbeat-событий в указанном порядке. Идентификатор конфигурации совпал у Python и JXA. Значит, handler зарегистрирован на controller configuration, переданной фактическому `WKWebView`; JavaScript увидел handler, а его стартовое сообщение реально принял delegate и записал writer.

В песочнице Codex WebKit может не создать своё служебное cache-хранилище и оборвать загрузку страницы. Это ограничение среды, а не доказательство поломки канала: тот же локальный запуск вне песочницы подтвердил полный startup-handshake.

#### Точная команда и оставшийся ручной клик

```bash
cd /Users/ilia/Documents/Gaia
rm -f /private/tmp/gaia-stage6-manual.jsonl
: > /private/tmp/gaia-stage6-manual.jsonl
GAIA_STAGE6_RUNTIME_DIAGNOSTICS=1 \
GAIA_STAGE6_DIAGNOSTICS_PATH=/private/tmp/gaia-stage6-manual.jsonl \
python3 -B app.py
```

До клика в журнале должны быть шесть событий выше. Затем один раз нажмите кнопку выбора файла и, если панель появится, нажмите «Отменить». Последовательность после `dom_file_input_click` покажет фактически достигнутый шаг: `webkit_file_picker_request`, `open_panel_started`, `open_panel_finished`, `completion_handler_called`, `webkit_upload_flow_received`. Не добавляйте в журнал или отчёт выбранные имена, пути либо содержимое файлов.

### Активация выбора файла 6.2

В трёх ручных проверках после полной startup-последовательности появлялся только прежний общий маркер `dom_file_input_click`; `webkit_file_picker_request` и callback UI-delegate не появлялись. Этот маркер означал click на самом `input`, а не факт запуска системной панели.

Установлены две независимые причины разрыва. На странице «Материалы» действие «Добавить материалы» отправляло только уже выбранные файлы, а не открывало выбор. Для выбора создана отдельная доступная нативная связь: единственный `journeyFiles` остаётся подключённым и активным `input[type=file]`, а видимый control — его `label for`. Он не использует `input.click()`, `dispatchEvent`, Promise, таймер или другую асинхронность, поэтому WebKit сохраняет исходный доверенный пользовательский жест. Поле визуально скрыто доступным способом и остаётся в порядке клавиатурной навигации; фокус отображается на label.

В JXA отдельно исправлен фактический adapter: `GaiaFilePanelDelegate`, как и диагностический delegate ранее, обязан экспортировать selector из словаря `methods`. Старый массив не предоставлял WebKit selector `webView:runOpenPanelWithParameters:initiatedByFrame:completionHandler:`. Runtime smoke теперь создаёт delegate и подтверждает этот selector.

Новые безопасные события различают уровни:

| Событие | Смысл |
| --- | --- |
| `upload_control_pointer_received` | Пользователь нажал нативный label выбора. |
| `file_input_activation_requested` | В том же доверенном event turn началась нативная label-активация input. |
| `file_input_click_event_received` | Click дошёл до самого input. |
| `webkit_file_picker_request` / `wkui_delegate_callback_received` | WebKit вызвал UI-delegate. |
| `open_panel_started`, `open_panel_result`, `completion_handler_called` | Состояние системной панели и завершение callback. |

Во все события попадают только разрешённые boolean, фиксированные коды и число выбранных URL. Не попадают имена, пути, содержимое файлов, DOM-текст, URL либо сырой event body.

Автоматически подтверждены нативная DOM-связь, отсутствие синтетической активации, selector-smoke UI-delegate и startup-handshake. Среда Codex не публикует JXA-окно через macOS accessibility, поэтому один физический клик и отмена панели остаются ручной проверкой. После шести startup-событий нажмите «Выбрать материалы», затем в открывшейся панели «Отменить». Ожидайте:

`upload_control_pointer_received` → `file_input_activation_requested` → `file_input_click_event_received` → `webkit_file_picker_request` → `wkui_delegate_callback_received` → `open_panel_started` → `open_panel_result` → `completion_handler_called`.

При отмене `selected_url_count` должен быть `0`, а `upload_flow_started` — отсутствовать. Успешный выбор нейтрального временного файла должен привести к `upload_flow_started` через прежний единый upload-flow; drag-and-drop не менялся.

### ABI `WKUIDelegate` 6.2a

После `dda53fb` ручной click подтвердил работу страницы до самого `input[type=file]`, но процесс `osascript` аварийно завершался до `webkit_file_picker_request`. macOS зафиксировала `EXC_BAD_ACCESS / EXC_ARM_PAC_FAIL` на главном потоке в цепочке `WebKit::UIDelegate::UIClient::runOpenPanel` → `ffi_closure_SYSV` → `JSOCInvocationClosureThunk` → `JSOCForwardInvocation` → `JSOCWrap`. Это означает, что WebKit нашёл selector и начал вызывать delegate, а JXA падал при маршалинге аргументов до входа в JavaScript-тело.

Runtime-аудит зарегистрированного `GaiaFilePanelDelegate` до исправления показал семь аргументов и контракт `v@:@@@@@`: `void`, `self`, `_cmd`, четыре object-аргумента и ещё один object. Это не соответствует API WebKit. Callback имеет только четыре явных аргумента: `WKWebView`, `WKOpenPanelParameters`, `WKFrameInfo` и completion block.

Исправленная JXA-регистрация использует четыре явных типа: `id`, `id`, `id`, `@?`. Runtime `NSMethodSignature` после исправления подтверждает:

| Индекс | Тип |
| --- | --- |
| 0 | `@` (`self`) |
| 1 | `:` (`_cmd`) |
| 2 | `@` (`WKWebView`) |
| 3 | `@` (`WKOpenPanelParameters`) |
| 4 | `@` (`WKFrameInfo`) |
| 5 | `@?` (completion block) |

Итоговый runtime type encoding: `v@:@@@@?`; число аргументов — 6, return type — `v`. Selector существует, class и instance отвечают на него. Раннее событие `wkui_delegate_body_entered` теперь является первой операцией тела callback; оно не имитируется другой частью pipeline.

Добавлен отдельный `--file-panel-harness`: он создаёт `NSApplication`, `WKWebViewConfiguration`, `WKWebView` с тем же delegate и локальную HTML-страницу с одним input. Harness создаётся без crash и пишет `file_panel_harness_ready`; он не вызывает delegate напрямую и не содержит Gaia backend или второй upload-flow. В среде Codex JXA-окно не доступно через accessibility, поэтому физический click в harness не автоматизирован. Ручной запуск harness либо Gaia после исправления должен подтвердить `wkui_delegate_body_entered` до `NSOpenPanel`.

Stop-condition ограничения JXA не достигнут: было найдено конкретное ABI-несоответствие. Native-host decision report в этой задаче не создавался.

### Запуск `NSOpenPanel` в JXA 6.2b

После исправления ABI ручной сценарий дошёл до `wkui_delegate_body_entered`, `webkit_file_picker_request`, `open_panel_created` и `open_panel_started`, но `osascript` завершался с `SIGABRT` до результата панели. Одинаковый crash при отмене и выборе файла указывал на необработанное Objective-C exception уже внутри тела JXA callback.

Первая операция после `open_panel_started` была `panel.runModal()`. Гипотеза подтвердилась: `runModal` не имеет явных Objective-C аргументов, а JXA представляет такой selector как property. Вызов со скобками создавал JavaScript-вызов вместо корректного Objective-C dispatch — тот же класс ошибки, который ранее был найден у `seekToEndOfFile()`.

Исправление минимально: используется `const result = panel.runModal;`. После возврата результат сравнивается как числовой `NSModalResponse`. Для отмены создаётся пустой Objective-C `NSArray`; для принятого выбора `panel.URLs` читается как `NSArray<NSURL *>` без строкового преобразования. Completion block вызывается ровно один раз непосредственно после подготовки этого массива.

Добавлены безопасные границы: `open_panel_runmodal_invocation_started`, `open_panel_runmodal_returned`, `open_panel_result_decoding_started`, `selected_urls_read_started`, `selected_urls_read_completed`, `completion_handler_invocation_started` и `completion_handler_called`. Они не содержат имени, пути, URL или содержимого файла.

Отдельный `--open-panel-smoke` создаёт только `NSApplication` и `NSOpenPanel`. После исправления он достиг `open_panel_runmodal_invocation_started` и оставался живым, без прежнего немедленного `SIGABRT`. Автоматизация Codex не получает JXA-диалог в accessibility, поэтому ручная отмена, реальный возврат `NSModalResponse`, callback completion и успешный upload ещё требуют одного ручного запуска. При отмене ожидается `open_panel_runmodal_returned` → `open_panel_result=cancelled` → `completion_handler_invocation_started` → `completion_handler_called` с `selected_url_count=0` и без `upload_flow_started`.

### Completion block `WKUIDelegate` 6.2c

После исправления `runModal` ручная отмена дошла до `open_panel_result=cancelled` и `completion_handler_invocation_started`, после чего JXA завершилась с `NSInternalInconsistencyException`: completion block не является JavaScript function. Это не повтор ABI-ошибки: runtime signature метода сохраняет `v@:@@@@?`, а тело callback и результат панели уже подтверждены.

Безопасная инспекция фиксирует только типовые признаки. Входящий block присутствует и объявлен как `@?`, но в JavaScriptObjC не callable и не представлен ObjC wrapper. Его адрес, описание и raw object не записываются. Прямой вызов, преобразование указателя, `eval` и libffi исключены.

Изолированный `--completion-block-bridge-harness` создаёт реальный Objective-C block Foundation и дважды передаёт его в JXA-метод, также объявленный `@?`. Оба получения дают одинаковый результат: block не callable и не ObjC-wrapped. Это воспроизводит границу bridge без WebKit, файла или пользовательских данных. JXA `registerSubclass` не может быть нативным helper: implementation такого метода выполняется в JavaScript и получает то же значение.

Stop-condition JXA выполнен. Безопасного способа вызвать completion block в этом runtime не найдено; callback нельзя корректно завершить из JXA. [Решение по замене только оконного host](/Users/ilia/Documents/Gaia/docs/STAGE_6_NATIVE_WINDOW_HOST_DECISION.md) сравнивает Swift, Objective-C и PyObjC. Рекомендуется отдельная задача на минимальный Swift host, который оставит Python backend, текущую web UI, API, drag-and-drop и единый upload-flow без изменений.

### Подтверждённый результат 6.1a (до восстановления канала)

После ремонта отдельный процесс Gaia снова был запущен и подтвердил `/api/runtime`; отдельный runtime smoke подтвердил, что диагностический delegate отвечает на обязательный selector. Автоматизация macOS по-прежнему не получила JXA-окно в доступном списке accessibility, поэтому физический DOM-клик не был выполнен. Следовательно, отсутствие аварии именно после DOM-клика и появление `dom_file_input_click` ещё требуют ручной проверки; ни `WKUIDelegate callback`, ни `NSOpenPanel`, ни completion handler, ни передача файла в upload-flow этим запуском не подтверждены. Это не является доказательством их неработоспособности; остаётся выполнить ручные шаги выше.

## Минимальный контур последующего исправления

До выполнения ручной проверки file picker исправление не выбирается. Для Veil минимальный отдельный анализ должен опираться на подтверждённую причину: модель сформировала одну непригодную находку, которая была строго отклонена. Нельзя исправлять проблему ослаблением схемы, координат, категорий или псевдонимов. После получения ручной последовательности file picker будущая задача должна изменять только первый фактически оборванный шаг, сохраняя `WKUIDelegate`, единый upload-flow и drag-and-drop.
# Нативный host этапа 6.3

Для `Gaia.app` диагностика включается только через `GAIA_NATIVE_HOST_DIAGNOSTICS=1` и явный JSONL-путь. Она фиксирует только код события, correlation id, ownership, port, HTTP status, результат, счётчики URL/completion, безопасный error code и длительность. Имена, пути и содержимое файлов, DOM, рабочие данные, environment и указатели не пишутся.

Этап 6.3a добавил раннюю последовательность до backend: `native_entry_reached`, `app_delegate_created`, `app_delegate_assigned`, `application_will_finish_launching`, `application_did_finish_launching`, `loading_window_created`, `loading_window_shown`, `backend_coordinator_started`. Она отделяет проблему AppKit lifecycle от Python, WebKit и file picker.

Launch-smoke 6.3a подтвердил обязательные события `native_entry_reached`, `application_did_finish_launching`, `loading_window_created` и `backend_coordinator_started`, после чего host валидно подключился к синтетическому loopback Gaia. Это не является физической приёмкой `NSOpenPanel`.

Этап 6.3b добавил accepted-path: `open_panel_presented`, `open_panel_result`, `selected_urls_collected`, `completion_handler_invocation_started`, `completion_handler_called`, а также page-события `file_input_click_received`, `file_input_input_event_received`, `file_input_change_event_received`, `file_list_received`, `upload_flow_started`, `upload_flow_completed` и `upload_flow_failed`. Разрешены только result, счётчики, main-thread flag, параметры панели, HTTP status и безопасный error code. Имена, URL, пути, расширения и содержимое файла запрещены.
