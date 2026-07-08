# API MVP

Актуальный MVP: `POST /api/analyze` создает асинхронный job, результат читается через `GET /api/jobs/<job_id>`. Focus также управляет проектами и группами памяти через Project Registry и хранит проектные диалоги через Project Dialogues.

HTML UI и JSON API отвечают с `Cache-Control: no-store`, чтобы рабочий
интерфейс не застревал в старой браузерной версии после перезапуска сервиса.

## Ошибки

Все JSON-ошибки возвращаются в едином контракте:

```json
{
  "error": {
    "code": "job_not_ready",
    "message": "job is not ready",
    "details": {},
    "trace_id": "gaia-1a2b3c4d5e6f"
  }
}
```

- `error.code` - стабильный машинный код для UI и contract-тестов.
- `error.message` - человекочитаемое описание.
- `error.details` - структурные детали, например имя неподдерживаемого файла.
- `error.trace_id` - короткий id конкретного ответа для диагностики.

Базовые коды: `not_found`, `invalid_request`, `unsupported_file_type`,
`empty_analyze_request`, `job_not_found`, `job_not_ready`,
`project_registry_error`, `conversation_error`, `invalid_multipart`,
`scribe_blocked`.

JSON request body ограничен `MAX_JSON_BODY_SIZE = 1_000_000` bytes. Multipart
request body ограничен `MAX_MULTIPART_BODY_SIZE = 100_000_000` bytes.
Превышение лимита возвращает structured error с `error.code = invalid_request`
или `invalid_multipart`.

## `GET /`

Возвращает браузерный интерфейс Focus. В интерфейсе есть выбор проекта, выбор профиля задачи, управление группами/проектами, верхний блок `Итог обработки`, проектная вкладка `Диалог`, верхняя вкладка `Память` для обновления памяти, `reviewPanel`, `promptPreview`, `reviewConfirm`, `copyBtn` и техническая вкладка `Диагностика`.

## `GET /api/projects`

Возвращает проекты из `paths.projects`, обогащенные registry-метаданными. Сервисная документация Gaia и `Контексты/Группы` не попадают в список проектов.

```json
{
  "projects": ["Автопретензии"],
  "project_records": [
    {
      "name": "Автопретензии",
      "code": "АПР",
      "title": "Автопретензии",
      "status": "active",
      "group_code": "DEV",
      "group_title": "Девелопмент",
      "context_inheritance": true,
      "health": "ok",
      "issues": []
    }
  ],
  "groups": []
}
```

Поле `projects` сохранено для обратной совместимости старого UI-контракта.

## Project Registry API

### `POST /api/projects`

Создает проектную структуру в Obsidian.

```json
{
  "code": "АПР",
  "title": "Автопретензии",
  "group_code": "DEV",
  "description": "Опциональное описание"
}
```

### `PATCH /api/projects/{name}`

Обновляет код, название, статус, группу и наследование. Если `code` изменился,
Gaia переименовывает файлы проекта с префиксом `<старый код> - `, включая
активные узлы `Память_Graph`, и обновляет markdown-ссылки/заголовки с этим
префиксом.

```json
{
  "code": "ДП",
  "title": "Новое название",
  "status": "active",
  "group_code": "DEV",
  "context_inheritance": true
}
```

### `POST /api/projects/{name}/validate`

Проверяет наличие `.gaia-project.json`, префиксных memory-файлов, `Память_Graph` и обязательных graph-папок.

### `POST /api/projects/{name}/repair`

Восстанавливает недостающие служебные файлы и папки без перезаписи существующей памяти.

### `POST /api/projects/{name}/archive`

Переводит проект в `archived`.

### `GET /api/groups`

Возвращает группы надпроектного контекста из `Obsidian Vault/Контексты/Группы`.

### `POST /api/groups`

Создает группу:

```json
{
  "code": "DEV",
  "title": "Девелопмент",
  "description": "Общие регламенты и шаблоны"
}
```

### `PATCH /api/groups/{code}`

Обновляет название, статус или описание группы.

### `POST /api/groups/{code}/archive`

Архивирует группу.

## `POST /api/analyze`

`multipart/form-data`:

- `project` - имя проектного пространства;
- `profile` - id профиля задачи, default `general`;
- `query` - текст запроса;
- `files` - 0..N файлов.

Multipart разбирается стандартной библиотекой Python через `email.parser`; Gaia
не зависит от deprecated `cgi.FieldStorage`.

Lore собирает эффективный контекст: групповой слой, затем проектная память. В prompt Forge групповой контекст описан как общие регламенты/методики, а проектная память имеет приоритет при конфликте.

Ответ `202 Accepted` содержит `job_id`, `status`, `message`, `progress`, `status_url`.
Job выполняется в bounded executor с `MAX_WORKERS = 4`; лишние задачи ждут
свободный worker вместо создания неограниченного числа потоков.

## `GET /api/jobs/<job_id>`

Когда `status = done`, `result` содержит `AnalysisPackage`.

Дополнительные поля после Project Registry:

- `result.group_code`;
- `result.group_title`;
- `result.group_sections`;
- `result.memory_sources[].scope` со значениями `group` или `project`.

Остальные ключевые поля: `profile_id`, `profile_title`, `prompt`, `journal_path`, `safety_audit_path`, `memory_sources`, `memory_total_sections`, `safe_for_codex_after_confirmation`, `local_fallback_required`, `policy_notes`, `query_mask_review`, `prompt_mask_review`, `files[].mask_review`.

`MaskReview` различает три состояния маршрута:

- `unresolved_pii = true` - конкретные ПД похожи на остаточные или Veil недоступен; внешний маршрут блокируется.
- `manual_confirmation_required = true` - текст тематически связан с ПД, но конкретные значения не найдены; внешний маршрут доступен только после ручного просмотра очищенного `prompt`.
- оба флага `false` - правила не нашли остаточного риска; ручное подтверждение перед копированием все равно обязательно.

`prompt_mask_review` - финальная проверка уже собранного prompt после Lore. Если выбранная память внесла ФИО, телефоны или другие ПД, Gaia маскирует именно итоговый `prompt`; при остаточном риске внешний маршрут блокируется.

## Project Dialogues API

### `GET /api/conversations?project=<name>`

Возвращает активные диалоги выбранного проекта из `service_docs/Диалоги`.
Архивированные диалоги не включаются в список.

### `GET /api/conversations/<conversation_id>`

Возвращает один диалог с rolling summary и messages.

### `POST /api/conversations`

Создает диалог для проекта:

```json
{
  "project": "Автопретензии",
  "title": "Опциональное название"
}
```

### `POST /api/conversations/<conversation_id>/messages`

Добавляет сообщение в существующий диалог. Endpoint собирает contextual query из
rolling summary, последних сообщений и нового текста, затем возвращает
обновленный `conversation`, свежий `package` и опциональный `local_result`.
Если `run_local=true`, `local_result.answer` сохраняется в истории диалога как
assistant message; UI показывает этот текст отдельной карточкой локального
ответа даже когда prompt был сокращен под окно локальной модели.

JSON-вариант:

```json
{
  "text": "Что дальше проверить?",
  "profile": "general",
  "run_local": false
}
```

Также поддерживается `multipart/form-data` с `text`, `profile`, `run_local` и
`files`.

### `POST /api/conversations/<conversation_id>/archive`

Помечает диалог как `archived`. Файл остается в service docs, но больше не
показывается в списке активных диалогов проекта.

## `POST /api/rebuild-prompt`

Пересобирает запрос для модели по готовой задаче обработки без повторной
обработки файлов, Echo или Veil. Endpoint принимает только `MemorySource.id`,
выбранные Lore в исходном `job.result`. Rebuild перечитывает и групповые, и
проектные узлы. В UI действие называется `Пересобрать контекст`.

## Обновление памяти / Scribe API

### `GET /api/scribe-inbox?project=<name>`

Возвращает файлы-кандидаты внутри выбранного проекта, которые еще требуют
обработки. Gaia исключает служебные файлы памяти, `Память_Graph`, скрытые
файлы, неподдерживаемые расширения, `ignored`/`indexed` items, служебный
`Автоизвлеченный текст` и исходники, которые уже упомянуты в
source/journal/graph memory. Поддерживаемые документы берутся из
`DOCUMENT_EXTENSIONS`.

Важно: список кандидатов не означает автоматическое чтение всех файлов в память.
Пользователь выбирает один файл и запускает действие UI `Разобрать выбранный
файл`, технически `POST /api/scribe-inbox/package`.
Построенные после этого предложения в память scoped to selected file: карточки
`source_summary` создаются из `files[]` package, а не из постороннего Lore
evidence проекта.

### `GET /api/scribe-inbox/preview?project=<name>&path=<relative>`

Возвращает preview файла по относительному пути внутри проекта. Для `.xlsx`
ответ содержит `excel.normalized_markdown`, список листов, заголовки, sample
строки, merged ranges и formula cells.

### `POST /api/scribe-inbox/package`

Готовит обычный `AnalysisPackage` из выбранного файла Inbox:

```json
{
  "project": "Автопретензии",
  "path": "Материалы/roadmap.xlsx",
  "profile": "memory_update",
  "instruction": "Опциональная инструкция"
}
```

Если `instruction` не передана, Gaia использует безопасную инструкцию по
умолчанию: выделять только устойчивые выводы и не переносить сырые строки
таблицы целиком. После подготовки item помечается как `prepared`.

### `POST /api/scribe-inbox/ignore`

Помечает файл как `ignored` в `service_docs/Scribe Inbox State`. Исходный файл
не удаляется. После успешного `POST /api/scribe-apply` для Inbox package Gaia
помечает исходник как `indexed`, поэтому он исчезает из списка новых файлов.

## `POST /api/scribe-draft`

Создает markdown-черновик обновления памяти для готового job или package,
полученного из диалогового turn. Проектная память не меняется.

```json
{
  "job_id": "20260703-...",
  "package": {}
}
```

## `POST /api/scribe-plan`

Строит staged plan обновления памяти для готового job или package из диалога.
План содержит карточки кандидатов, destination, confidence, evidence, target
path и preview. Live-память не меняется.

Для package с файлами Scribe разделяет provenance и смысловую память:
`source_summary` описывает обработанный источник без переноса сырого текста, а
semantic enrichment добавляет отдельные карточки для веток графа. Такие
карточки должны быть пригодны как будущие memory nodes: они содержат суть,
контекст, способ использования в Gaia и короткое evidence. Если локальный
classifier недоступен или не вернул JSON-кандидатов, deterministic enrichment
все равно может создать review-кандидаты из устойчивых сигналов в
`files[].masked_text`.

Если `files[].name` выглядит как hash, digest, числовой id или иной служебный
slug, Scribe не использует его как название memory-узла. План строит
content-based source-summary: короткий `title` берется из первой смысловой
строки `files[].masked_text`, а исходное имя и путь остаются только в
`Evidence`/`Provenance`. Если извлеченный текст есть, но смысловое имя получить
нельзя, карточка остается `skip/exclude`, чтобы не записывать hash/generic node
в live-память.

```json
{
  "job_id": "20260703-...",
  "package": {}
}
```

Если есть unresolved PII или пакет требует ручной/локальной проверки, endpoint
возвращает блокировку.

## `POST /api/scribe-apply`

Применяет только явно выбранные карточки предложений Scribe. Перед записью создает
backup активной памяти в service docs, затем создает новые graph nodes и
append-only записи в source registry, journal и graph index.

```json
{
  "job_id": "20260703-...",
  "package": {},
  "selected_item_ids": ["abc123"]
}
```

Scribe apply не выполняет автоматический merge существующих decisions и не
снимает safety-блокировки.

## Остальные endpoints

- `GET /api/profiles`;
- `POST /api/scribe-draft`;
- `POST /api/scribe-plan`;
- `POST /api/scribe-apply`;
- `GET /api/scribe-inbox?project=<name>`;
- `GET /api/scribe-inbox/preview?project=<name>&path=<relative>`;
- `POST /api/scribe-inbox/package`;
- `POST /api/scribe-inbox/ignore`;
- `GET /api/conversations?project=<name>`;
- `POST /api/conversations`;
- `POST /api/conversations/<conversation_id>/messages`;
- `POST /api/conversations/<conversation_id>/archive`;
- `POST /api/local-answer`;
- `GET /api/local-status`;
- `POST /api/launch`.

## Проверка

Команда:

```bash
cd Local_Analytics_System
python3 -B -m unittest discover -s tests
python3 -B -m gaia.config
```

Покрытие: masking, профили, jobs, server contracts, lazy external imports,
локальный статус, Project Registry, Project Dialogues, Scribe Inbox, Excel
preview, memory index, prompt rebuild, Scribe, retention и UI review.
