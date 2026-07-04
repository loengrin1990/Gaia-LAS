# Project Registry

## Роль

Project Registry управляет проектными пространствами памяти Gaia поверх файловой структуры Obsidian. Источник правды остается в vault, а не в отдельной базе данных.

Код: `Local_Analytics_System/gaia/projects.py`.

## Сущности

`ProjectRecord` описывает проект:

- `name` - имя папки проекта в `Проекты`;
- `code` - стабильный короткий код для префиксных файлов;
- `title` - человекочитаемое название;
- `status` - `active`, `draft` или `archived`;
- `group_code` и `group_title` - основная группа проекта;
- `context_inheritance` - включает наследование группового контекста;
- `health` и `issues` - результат структурной проверки.

`ProjectGroup` описывает надпроектный контекст:

- `code` и `title`;
- `context_path` - `<код> - Контекст.md`;
- `sources_path` - `<код> - Источники.md`;
- `journal_path` - `<код> - Журнал.md`;
- `materials_path` - папка `Материалы`.

## Файловая структура проекта

При создании проекта Gaia создает:

```text
Проекты/<Название проекта>/
  .gaia-project.json
  <CODE> - Память.md
  <CODE> - Источники.md
  <CODE> - Журнал памяти.md
  Исходники/
  Память_Graph/
    <CODE> - Индекс памяти.md
    00_Core/
    10_Branches/
    20_Decisions/
    30_Open_Questions/
    40_Risks/
    50_Sources/
    90_Archive/
```

`update_project()` поддерживает смену кода проекта. При изменении кода Gaia
переименовывает все файлы проекта с префиксом `<старый код> - ` на
`<новый код> - ` и обновляет markdown-ссылки/заголовки с этим префиксом.

`repair_project()` восстанавливает только недостающие служебные файлы и папки. Существующие memory-файлы не перезаписываются.

## Файловая структура группы

Группы лежат отдельно:

```text
Obsidian Vault/Контексты/Группы/<GROUP_CODE>/
  .gaia-group.json
  <GROUP_CODE> - Контекст.md
  <GROUP_CODE> - Источники.md
  <GROUP_CODE> - Журнал.md
  Материалы/
    Регламенты/
    Шаблоны/
  Память_Graph/
```

Групповой контекст используется для регламентов, шаблонов, методик, общих ограничений и правил качества, которые распространяются на несколько проектов.

## API

- `GET /api/projects` - возвращает совместимый список `projects`, новые `project_records` и `groups`.
- `POST /api/projects` - создает проект.
- `PATCH /api/projects/{name}` - обновляет название, статус, группу и наследование.
- `POST /api/projects/{name}/validate` - проверяет обязательные файлы и папки.
- `POST /api/projects/{name}/repair` - восстанавливает недостающую структуру.
- `POST /api/projects/{name}/archive` - переводит проект в `archived`.
- `GET /api/groups` - список групп.
- `POST /api/groups` - создает группу.
- `PATCH /api/groups/{code}` - обновляет группу.
- `POST /api/groups/{code}/archive` - архивирует группу.

## Проверки

Автотесты: `tests/test_project_registry.py`, `tests/test_memory_index.py`, `tests/test_rebuild_prompt.py`.
