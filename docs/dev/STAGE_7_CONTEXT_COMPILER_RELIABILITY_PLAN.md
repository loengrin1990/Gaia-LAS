# План надёжной сборки контекста Stage 7

## Checkpoints

- [x] A. Воспроизведение `CONTEXT_JSON_PARSE` и безопасная диагностика.
- [x] B. Route-конфигурация, структурное chunking и чистые unit-тесты.
- [x] C. Оркестрация compiler: retry/split, дедупликация, атомарность и receipt.
- [x] D. Асинхронная job, API, отмена и UI-прогресс.
- [x] E. Исполняемый Node harness и сквозная проверка.
- [x] F. Локальный Ollama smoke на синтетических данных.
- [x] G. Полные регрессии, документация и corrective commit.

## Критерии готовности

Каждый checkpoint закрывается только после относящихся к нему автоматических тестов. В итоговой проверке должны быть подтверждены изоляция workspace, отсутствие частичных записей при ошибке или отмене, отсутствие raw prompt/answer в диагностике, успешная работа chunking с глобальными координатами и сохранение Stage 7 API-контрактов.

## Текущий статус

Завершено: corrective review reliability. Перед commit выполнены целевые Python/API/E2E/Node-проверки, syntax check и native host build.
# Runtime contract

Для `context_compiler` выбран локальный профиль `ollama_qwen3_14b` / `qwen3:14b`: Ollama `/api/chat`, полный schema object, `think=false`, `num_ctx=16384` и temperature 0. Автоматическое переключение моделей не добавляется. Local override не должен заменять schema mode или отключать этот contract.
