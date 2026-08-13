# Local LLM Resource / Privacy Benchmark — Phase 1

Дата замера: 2026-08-13. Этот документ — operational report конкретной машины, а не нормативный контракт и не изменение runtime-конфигурации.

## Scope и границы

Проверен только локальный `qwen3:14b` через Ollama `127.0.0.1:11434/api/chat`. Во всех запросах использованы только synthetic данные; внешних/cloud вызовов и рабочих материалов не было. Product code и `config.json` не изменялись.

Замер выполнен на MacBook Pro M5, 24 GB unified memory, macOS 26.5.2 (arm64), Ollama 0.32.9. Установленная модель: `qwen3:14b`, ID `bdbd181c33f2`, 9.3 GB на диске. Перед началом было 435 GiB свободного SSD и 78% свободной системной памяти.

Текущий runtime Gaia задаёт 8k для общего provider `ollama_qwen3_14b`; у route `context_compiler` отдельный `num_ctx=16384`. Это не менялось данным benchmark.

## Методика

Для каждого запрошенного окна (8k, 16k, 32k, 64k) выполнены одни и те же synthetic scenarios:

- короткий ответ по current Operational Context;
- различение current OC и historical Memory;
- конфликт двух authority-фактов без самостоятельного выбора winner;
- restricted handling с запретом внешнего route;
- длинный пакет с тысячами разных нерелевантных synthetic units и разнесёнными current, historical, restricted и conflict фактами.

Ответы оценивались детерминированно: JSON и ожидаемые инварианты, а не LLM-as-judge. Для long package требовались оба временных факта, `restricted` и `unresolved`. Метрики получены из streaming API Ollama, `ollama ps`, `memory_pressure` и `vm_stat`. Swap-счётчики macOS системные и накопительные, поэтому они показывают давление за период, но не могут быть приписаны только Gaia.

## Результаты

| Запрошено `num_ctx` | Фактически обработано long input | Время до первого output | Полное время long run | Long quality | Наблюдение |
|---|---:|---:|---:|---|---|
| 8,192 | 4,098 tokens | 15.8 s | 20.2 s | fail | потеряны оба удалённых временных authority-факта |
| 16,384 | 8,194 tokens | 36.8 s | 41.5 s | fail | потеряны оба временных authority-факта |
| 32,768 | 16,386 tokens | 100.0 s | 106.6 s | fail | потеряны оба временных authority-факта |
| 65,536 | 20,482 tokens | 146.0 s | 151.7 s | fail | Ollama фактически загрузил окно 40,960, а не 65,536 |

Во всех четырёх окнах прошли короткие deterministic checks current/history, unresolved conflict и restricted handling. Их типичная скорость генерации была около 12–15 tokens/s. Это не компенсирует failure long package: для OC-3 → OC-5 нельзя считать такой результат полным и нельзя silently выбрасывать потерянные authority facts.

`64k` не является поддержанным штатным режимом на этой машине: после запроса `num_ctx=65536` `ollama ps` показал `CONTEXT 40960`. Он не прошёл long quality и оставил лишь 9% свободной системной памяти (baseline до нагрузки — 78%).

Для 16k выполнен дополнительный uncached повтор с другим synthetic corpus: 43.2 s против исходных 41.5 s. Признаков монотонной thermal-деградации по двум uncached прогонам не получено, но этого недостаточно, чтобы доказать комфортный длительный режим. Идентичные повторы Ollama закэшировал (около 5 s), поэтому они исключены из latency/thermal вывода.

После всей серии и явной выгрузки модели свободная память восстановилась только до 31%. За период system-wide swap counters выросли на 317,716 swap-ins и 474,889 swap-outs (страницы по 16 KiB). Manual probe отзывчивости macOS не выполнен, поэтому beachball/UI-lag не оцениваются как проверенные факты.

## Решение для проектирования OC-5

**Gate Phase 1: BLOCK для выбора single-pass full-package envelope.** Ни 8k, ни 16k, ни 32k, ни технически ограниченный 40,960-context режим не доказали сохранение всех разнесённых authority-фактов в long package. `32k` и выше также не являются комфортным interactive режимом по latency и memory pressure; `64k` не имеет практического смысла для штатного private/restricted workflow на M5/24 GB.

До отдельного принятого решения OC-5 должен сохранять product rule:

1. local full processing — только когда пакет помещается в заранее проверенный envelope с полным deterministic quality result;
2. иначе safe staged/chunked local processing, где current OC, historical Memory, conflicts и handling передаются отдельными typed units и итог явно маркируется как staged;
3. если смысл и полнота не сохраняются — explicit inability/request narrowing.

Недопустимы silent omission authority, автоматическое разрешение конфликта, понижение sensitivity, переход в cloud/external route или выдача partial как полного. Этот benchmark не меняет действующий `context_compiler` contract с 16k: он показывает, что OC-5 full-package runtime нельзя проектировать как простое увеличение `num_ctx`.

## Следующее решение архитектора

Для открытия OC-5 нужен отдельный accepted contract staged disclosure/runtime и повторный benchmark уже для конкретного typed package renderer. Он должен измерять полный retained authority набор, а не только заявленный размер окна.
