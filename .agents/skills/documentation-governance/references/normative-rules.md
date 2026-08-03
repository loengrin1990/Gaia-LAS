# Нормативные правила

## Классы

- `genre`: `normative_rules`, `reference`, `runbook`, `decision_analysis`, `handoff_state`, `agent_instruction`.
- `authority`: `normative`, `descriptive`, `historical`, `operational`.

Жанр описывает назначение документа; authority — как с ним обращаться при расхождении с implementation. Эти признаки не выводятся друг из друга.

## Правила authority

`normative` задаёт обязательное правило. Противоречие implementation, config или runtime — `CONTRACT_VIOLATION`; документ нельзя автоматически приводить к коду.

`descriptive` описывает доказанное текущее устройство. При доказанном расхождении допустим `DOC_DRIFT` и targeted sync документа.

`historical` сохраняет решение, rationale, acceptance или прошлое состояние. Не переписывай историю под актуальный runtime.

`operational` — живое состояние/handoff. Его можно обновлять вместе с текущим проектом, но он не может переопределять normative документ.

Особо строго проверяй privacy/safety, storage/provenance, workspace isolation, export restrictions, принятые architecture decisions и security invariants.
