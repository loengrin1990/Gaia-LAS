# Gaia — bounded integration audit

Дата: 13.11.2026. Authority: historical verification + предложенный план. Проверяемая production-code feature: `f49e53c561400f688d5213a2b1a2001501f96a62`. Этот audit добавляет только smoke/tests/docs, не меняет `gaia/**`. Это не приёмка rollout и не решение merge.

## Точная база и состав

Remote проверен read-only: опубликованный `main` — `1e3b95a03a7bf8073aab055996f5aa8c661aa190`. Локальный `main` — `0667f94a52f16b26239d87fe71a90dd547f51bd8`, его единственный родитель `1e3b95a`. Наличие имени main не доказывает текущий runtime или готовность продукта.

Рабочий `/Users/ilia/Documents/Gaia`: `codex/local-model-profile-qwen9b`, HEAD `1f1e43340a48a4e64f4c3588b92dee00b8820fe6`, родитель `0667f94`; dirty. Общая база с `f49e53c` — **0667f94**. Feature содержит 13 коммитов после базы; рабочая ветка содержит один соседний Qwen-коммит. `f49e53c` на 14 коммитов впереди опубликованного main; обычное подключение к рабочему HEAD не является fast-forward. Branch/worktree не переключались; Git/index рабочего checkout не изменялся.

| Входит в f49e53c | Commit(s) | Поставляемая граница |
|---|---|---|
| Сохранённый memory pilot | d30b695 | отдельный ContextDesk |
| Адресные правки, review UX, remainder, holdout | 78534ff, 738811c, f485845, 9468dc6 | opt-in пилот, не гарантия смысла |
| Cancel compute slot | 76ea450 | слот до фактического завершения |
| Узкий synthetic Tristan bridge | 9699178, 4821d90 | изолированный API protocol/runner |
| Synthetic browser review | f43da7e | отдельный review, не main runtime |
| Synthetic ContextDesk history | b0b04d4 | изолированное связанное пространство |
| Два synthetic прохода | 89dc6f1 | bounded trial, не общий живой workflow |
| Основной поиск материалов | d5fc03d | реальный main UI + read-only source view |
| Основной локальный пакет/основания | f49e53c | существующий Dialogue/Assembler, без модели/внешних прав |

**Не входят:** Qwen `1f1e433` (README, два config examples, Obsidian README, local-status tests); Veil `b1a39b7c9f3e09269657b9ea97ab79ed5b95c4d0` (родитель **1e3b95a**, не 0667f94); dirty quote slice после d30b695; незакоммиченные UI/project-status и recovery-материалы главного checkout. Actual `config.json`, модели, реальные данные и budgets не переносились.

## Конфликты и отложенная работа

Проведена read-only трёхсторонняя **пофайловая** диагностика `git merge-file --stdout` на временных копиях кода; результат никуда не применялся, branch merge/index/tree не создавались. Это проверка текстового совмещения, не доказательство совместимости поведения.

Committed Qwen против feature от общей базы: 5 файлов совмещаются текстово без конфликта. Отдельно текущий dirty patch от его HEAD `1f1e433` против feature даёт:

| Dirty tracked файл | Результат |
|---|---|
| docs/USER_INTERFACE.md | **1 конфликтный блок**; объединить описания UI, не потерять новый workflow |
| tests/test_context_compiler.py | **1 конфликтный блок**; сохранить смысл lifecycle/assertions обеих правок |
| tests/test_local_status.py | **1 конфликтный блок**; fixtures должны быть независимы от реального профиля, Qwen-проверки повторить |
| gaia/static/index.html | текстово совместим; project-status labels и новые handlers требуют совместного UI-прогона |
| README.md | текстово совместим; сохранить Qwen-профиль и текущие инструкции |
| tests/test_conversations.py | буквально совпадает с feature; не накладывать повторно |
| tests/test_scribe.py | буквально совпадает с feature; не накладывать повторно |

Untracked `.github/` и `veil_desktop/`: все **18** файлов сохранённого Veil-коммита существуют и совпадают byte-for-byte; не удалять как «дубли». Untracked `tests/test_project_status_ui.js` связан с dirty HTML-подписями; его нет в feature. `docs/dev/2026-11-12-context-recovery.md` — отдельный сохранённый контекст. Поэтому в feature пока виден прежний raw `needs_attention`; пользовательское исправление подписи не включалось молча.

Quote worktree `/Users/ilia/.codex/worktrees/3fc9/Gaia`, branch `codex/project-memory-pilot`, d30b695: четыре tracked dirty-файла (`docs/USER_INTERFACE.md`, `gaia/context_desk.py`, `gaia/desk_analysis.py`, `tests/test_desk_analysis.py`) текстово совмещаются без конфликтов, но **не входят и не квалифицированы совместно**. Untracked `docs/dev/2026-11-12-memory-update-reliability.md` также остаётся там. Отсутствие текстового конфликта не разрешает автоматически включать иной quote slice.

## Проверенная целостность и проба

Production-code blocker основного пути не найден. Расширен уже существующий E2E-тест: настоящий Handler проводит synthetic upload → review/новую версию/confirm → Compiler → принятие → сводку → Dialogue package без модели → тот же native source/block link → read-only основание. Pending/rejected исключены; store не меняется при чтении. Используются deterministic model doubles, не настоящая модель.

Full offline: **676 Python + 60 Node**, 0 failures/errors/skips/cancelled. Standalone smoke1 pass. Browser основного UI из repeatable `--serve`: принятое/ожидающее в поиске → пакет с одним принятым фактом → полное основание; console errors0. Это инженерная симуляция. Качество модели, полный ручной picker, личный UX feedback и real-data qualification не доказаны. Без новых платных/внешних model calls; scripts/budgets старого Tristan не менялись.

Запуск и 2–3-минутный сценарий имеют один operational home: [E2E runbook](../E2E_VALIDATION.md#повторяемая-проверка-основного-пути-без-модели). Runtime8796 не опрашивался и не менялся. Own trial остановлен, temporary profiles0 после stop. Рабочие данные не копировались.

## Минимальный путь подключения — план, не выполненные действия

1. **Уже можно:** запустить smoke и temporary UI из этой изолированной feature-копии. Не нужны выбор модели, внешние права или изменение рабочего checkout. Проверки и личная проба не требуют deployment.
2. **Нужно решение scope/приёмки:** подключать весь накопленный stack (включая выключенные пилотные инструменты) либо готовить отдельную core-only линию. Второй вариант не равен двум cherry-pick: нужны отдельная проверка зависимостей и санкционированный scope. Этот audit выбор не принимает.
3. При выборе полного stack и отдельном разрешении integration: сначала сделать backup/сохранить 7 tracked и 4 untracked записи главного checkout и 5 записей quote worktree, включая метаданные версий. Затем создать **новый чистый integration worktree от 1f1e433**, сохранив Qwen-коммит и текущий checkout на месте. Это будущая операция; сейчас новый integration result не создавался.
4. Только после явного разрешения объединить approved feature ancestry в чистой integration-ветке без переписывания истории. Вручную разобрать три конфликтных файла и согласовать dirty HTML с его UI-тестом. Совпадающие tests не применять второй раз. Quote и Veil оставить отдельно, если не приняты отдельными решениями. Не копировать feature поверх dirty папки и не использовать blanket checkout/reset.
5. На итоговом integration-коде повторить smoke, полный Python/Node regression, UI statuses/search/source/package/cancel/repeat checks и GitNexus/documentation sync. Профили providers не менять автоматически. Модельная/real-data квалификация — отдельная приёмка, не результат этих doubles.
6. Переключение рабочего запуска, storage path и rollout — отдельное явное разрешение после review итогового SHA и сохранённых данных. Ни merge, rebase, cherry-pick, main push, deployment, ни изменение рабочего config в этом audit не выполнялись.
