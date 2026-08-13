# Local LLM Resource / Privacy Benchmark — Phase 2

Дата замера: 2026-08-13. Это operational report конкретной машины, а не изменение runtime-конфигурации или нормативного контракта.

## Вывод

**Лучший проверенный кандидат для M5 / 24 GB — `qwen3.5:9b` (ID `6488c96fa5fa`) с `num_ctx=16384` как комфортным штатным envelope.** Он стабильно сохранил все truth/privacy-инварианты в трёх независимых long-package повторах и оказался быстрее и экономнее `gemma4:12b`.

`qwen3.5:9b` также прошёл exploratory Round C на `32768`, однако это **HEAVY envelope**, а не новая runtime-рекомендация: без отдельного принятого product contract нельзя менять Gaia configuration или переходить к OC-5.

Round B не понадобился: два primary-кандидата уже дали надёжный 16k quality pass. `gpt-oss:20b` не квалифицировался: в данном Ollama API-режиме он не вернул пригодный JSON даже для коротких deterministic checks, а также создал наибольшее memory pressure.

## Scope, сохранность и среда

- Baseline checkout: `43760ea0bb73ff16cf5f4fa36e0cc5ec25738750`.
- Только synthetic facts, local Ollama `127.0.0.1:11434`, без cloud inference, product-code и configuration changes.
- `ollama pull`, изменение тегов и обновление моделей не выполнялись.
- MacBook Pro M5, 24 GB unified memory, macOS 26.5.2 arm64, Ollama 0.32.9.
- Стартовая свободная системная память: 80% (основная серия); свободное пространство и raw prompts/answers в отчёт не записывались.

Зафиксированные primary identities до замера:

| Model | ID | Installed size | Model metadata |
|---|---|---:|---|
| `gemma4:12b` | `4eb23ef187e2` | 7.6 GB | 11.9B, Q4_K_M, advertised 262144 context |
| `gpt-oss:20b` | `17052f91a42e` | 13 GB | 20.9B, MXFP4, advertised 131072 context |
| `qwen3.5:9b` | `6488c96fa5fa` | 6.6 GB | 9.7B, Q4_K_M, advertised 262144 context |

Heavy candidates were only inspected, not run: `qwen3.6:27b` (`a50eda8ed977`) and `gemma4:26b` (`5571076f3d70`), both 17 GB installed.

## Методика и ограничение сопоставимости

Phase-1 raw synthetic fixtures не были сохранены в checkout, поэтому точное побайтовое повторение невозможно. Воспроизведены те же пять semantic categories и их required invariants:

1. различение current Operational Context и historical Memory;
2. сохранение `unresolved` ambiguity без выбора winner;
3. `restricted` handling без external route;
4. запрет представить явно неполный пакет как complete;
5. длинный пакет с разнесёнными required authority facts, конфликтом и restricted handling.

Каждый ответ проверялся детерминированно как JSON по literal required fields. Long-package pass означал одновременное наличие обоих authority facts, `unresolved`, `restricted`, отсутствия invented authority и `complete=true`. Ни один aggregate score не отменял invariant failure.

Для каждого primary model выполнены 8k и 16k: четыре short checks и три long-package повтора. Изначально одинаковые запросы дали prefix-cache hits, поэтому устойчивость подтверждена отдельной контрольной серией: три long-package запуска с разными нерелевантными synthetic fillers. В таблицах latency приведён именно этот **uncached контроль**. `prompt_eval_count` — фактически обработанный prompt по ответу Ollama; он важнее заявленного `num_ctx`.

Для всех моделей был явно задан единый совместимый baseline: `temperature=0`, `format=json`, `think=false`, `num_predict=220`. Это не скрытая оптимизация под лидера. После failure GPT-OSS выполнен один compatibility probe без `think=false`: модель вернула reasoning, но при лимите 40 completion tokens остановилась на `length` и оставила `message.content` пустым. Для честного сравнения Gaia JSON-contract в этом режиме не был заменён и не был «подогнан».

## Round A: качество и надёжность

| Model | 8k: short checks | 8k long | 16k: short checks | 16k long (uncached) | Reliability |
|---|---|---|---|---|---|
| `gemma4:12b` | 4/4 pass | 0/3, stable fail; фактически 4,099 prompt tokens | 4/4 pass | 3/3 pass; 14,928 tokens | 16k stable pass |
| `gpt-oss:20b` | fail: пустой `message.content` в JSON mode | 0/3, stable fail; 4,098 tokens | fail: пустой `message.content` | 0/3, stable fail; 11,627 tokens | stable fail |
| `qwen3.5:9b` | 4/4 pass | 0/3, stable fail; 4,098 tokens | 4/4 pass | 3/3 pass; 15,360 tokens | 16k stable pass |

Таким образом, **ни один primary model не даёт надёжного полного long-package ответа на 8k**. Это согласуется с Phase 1: меньшая модель или лучшее качество не отменяет физическое усечение самого long input примерно до 4.1k tokens.

На 16k `gemma4:12b` и `qwen3.5:9b` не смешивали current/history, не придумывали winner, не понижали sensitivity, не добавляли authority facts и не представляли partial как complete: 3/3 uncached complete pass у каждой.

## Производительность и ресурсы

| Model / effective context | Uncached long TTFT | Full duration | Generation | Loaded model / device | Memory after its 16k series |
|---|---:|---:|---:|---|---:|
| `gemma4:12b` / 14,928 | 71.9–79.5 s | 75.9–86.1 s | около 11.8 tok/s | 8.4 GB, 100% GPU | 17% free |
| `qwen3.5:9b` / 15,360 | 44.6–46.4 s | 47.9–50.0 s | 16.5–16.9 tok/s | 6.0 GB, 100% GPU | 24% free |
| `gpt-oss:20b` / 11,627 | 23.4 s до пустого output | 23.4 s | 24.3 tok/s | 12 GB, 100% GPU | 20% free |

`gpt-oss:20b` не получает преимущества от меньшей latency: результат не прошёл truth/privacy JSON gate. В его 8k серии свободная память падала до 12%, а system-wide swapouts выросли на 178,240 страниц по 16 KiB. Swap-счётчики накопительные и системные, поэтому это наблюдение о давлении за интервал, а не утверждение, что вся величина создана Gaia.

После всех main и uncached control серий свободная память была 24%. Каждая модель размещалась на GPU; RSS runner process недоступен в ограниченном системном интерфейсе, поэтому модельная память зафиксирована по `ollama ps`, а не подменена оценкой процесса.

## Round B

**Не выполнялся и не требуется по заданному trigger.** Primary candidates уже дали reliable 16k quality pass. Тестирование 17 GB `qwen3.6:27b` или `gemma4:26b` не добавило бы нужного различения между model-capacity failure и single-pass architecture failure, зато несло бы повышенный риск memory pressure на 24 GB машине.

## Round C: Qwen 3.5 9B at 32k

Кандидат допущен, потому что надёжно прошёл 16k. В трёх uncached long-package runs:

- фактически обработано по 15,360 prompt tokens;
- все short checks 4/4 pass, long checks 3/3 pass (`stable pass`);
- TTFT: 40.6–46.5 s; full duration: 43.3–49.2 s; 18.5–18.8 tok/s;
- `ollama ps`: 6.6 GB, 100% GPU, actual context 32,768;
- свободная память: 26% до и 23% после серии; swapins +15,585, swapouts +16,636 system-wide pages.

На данном пакете 32k не расширил фактически нужный input выше 15,360 tokens, поэтому этот результат доказывает **viability of the 32k runtime envelope**, но не ценность более длинного single-pass package. Он заметно тяжелее 16k без доказанного quality gain для текущего fixture.

## Ответы на decision questions

1. **8k:** short invariants проходят у Gemma 12B и Qwen 3.5 9B, но long-package quality не проходит ни у одного primary candidate. GPT-OSS не проходит JSON-contract.
2. **Надёжные 16k:** Gemma 12B и Qwen 3.5 9B — обе 3/3 uncached long complete pass. GPT-OSS — stable fail.
3. **Лучшее качество в M5/24 GB envelope:** Qwen 3.5 9B. Оно равно Gemma по проверенным invariants и лучше по TTFT, total latency, generation speed и memory headroom.
4. **Цена лидера на 16k:** около 6.0 GB model residency, 100% GPU; 44.6–46.4 s TTFT и 47.9–50.0 s full long request; после серии 24% free memory. Это подходит для bounded local package, но не является мгновенным UI interaction.
5. **Решил ли рост модели Phase-1 failure:** нет как общий принцип. 8k всё ещё усекал long input и давал stable fail. Смена на лучше подходящую 9B/12B модель решила конкретный bounded 16k authority-retrieval test, не делает неограниченный full-package processing безопасным.
6. **Round B:** не требовался; primary результат уже отделил ограничение 8k input-envelope от качества моделей и дал 16k reliable pass.
7. **32k с победителем:** технически viable и quality-pass для данного 15,360-token пакета; оставлять его HEAVY/diagnostic режимом. Отдельный product contract должен решить, есть ли причина использовать его вместо более экономного 16k.

## Остаточный gate

Phase 2 снимает модельный quality block для **bounded synthetic package at 16k** и определяет предпочтительный кандидат. Он не открывает OC-5, не отменяет Phase-1 conclusion о небезопасности «увеличить окно и надеяться» и не заменяет required staged-disclosure/runtime contract. Реальные данные, cloud/external routing и product integration этим замером не проверялись.
