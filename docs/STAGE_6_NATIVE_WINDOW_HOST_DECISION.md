# Решение по оконному host этапа 6

## Решение

JXA пригоден для запуска `WKWebView`, показа `NSOpenPanel` и обработки Objective-C object-аргументов. Но на используемом arm64e runtime JavaScriptObjC передаёт входящий completion block `@?` в `ObjC.registerSubclass` как непригодное для вызова JavaScript string-значение. Поэтому JXA не может безопасно завершить `WKUIDelegate` file picker.

Это не проблема Python backend, HTML, WebKit upload-flow или `NSOpenPanel`. Следующая отдельная задача должна заменить только JXA оконный host на минимальный нативный host, сохранив локальный Python server и текущую web UI.

## Доказательства

- `WKUIDelegate` имеет корректный runtime encoding `v@:@@@@?`.
- WebKit входит в callback, панель открывается и `runModal` возвращает результат.
- Реальный callback завершился сообщением `completionHandler is not a function`.
- Изолированный JXA harness дважды передаёт реальный Objective-C block `@?` в JXA-метод: block присутствует, но не callable и не ObjC-wrapped.
- Вызов адреса, ручной FFI и преобразование указателя исключены как небезопасные.

Apple документирует, что блоки являются Objective-C objects, а `WKUIDelegate` требует вызвать completion с выбранными URL либо `nil` после закрытия панели. Фактическое поведение JavaScriptObjC в этом callback не даёт для block безопасного callable-представления.

## Варианты

| Вариант | ABI и completion block | Сборка и запуск | Влияние | Вывод |
| --- | --- | --- | --- | --- |
| Минимальный Swift executable | Нативный вызов block и `WKUIDelegate` надёжны. | Нужен Xcode/Swift toolchain для сборки; пользователю нужен готовый бинарник. | Заменяет только JXA окно, оставляет Python server, URL и web UI. | Предпочтительный вариант. |
| Минимальный Objective-C executable | Так же надёжен для ABI; близок к текущим Cocoa вызовам. | Нужен Xcode/clang и упаковка бинарника. | Такой же узкий контракт. | Допустим, но Swift удобнее для дальнейшего сопровождения. |
| PyObjC host | Технически может вызвать block нативно. | Не является штатной зависимостью Gaia; добавляет тяжёлую Python/macOS-зависимость и упаковочный риск. | Меняет окружение launcher. | Не выбирать без отдельного подтверждения. |

## Граница следующей задачи

Нативный host должен только создать `WKWebView`, открыть существующий URL локального Python-сервера и реализовать `WKUIDelegate`. Он не должен содержать бизнес-логику Gaia, backend API, новую загрузку, обработку drag-and-drop или работу с данными файлов. При отмене он передаёт `nil`; при выборе — исходный `NSArray<NSURL *>` в completion block ровно один раз.
