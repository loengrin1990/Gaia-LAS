# Operational Context Retrieval v0

Этот документ описывает реализацию OC-2. Его нормативные правила задаёт [Operational Context v0](OPERATIONAL_CONTEXT_V0.md); write semantics OC-1 остаются в [Store](OPERATIONAL_CONTEXT_STORE.md).

OC-2 отвечает на вопрос: «Что Gaia вправе использовать локально, чтобы определить текущий authority?» Это trusted-local authority retrieval, а не фильтр раскрытия для downstream consumer.

`OperationalContextReader` принимает exact `user_ref`, `project_ref`, `system_ref`, supported kinds, typed policy разрешённых уровней handling внутри trusted-local boundary Gaia, bounded budget и optional exact subject refs задачи. Policy — внутренний контекст Gaia: пользователь не должен помнить sensitivity ранее добавленного материала. Он открывает только три вычисленные store partitions для этих identity; нет wildcard, fallback, legacy Context read или cross-project enumeration.

Перед возвратом reader последовательно проверяет persisted state/evidence OC-1, lifecycle/confirmation, trusted-local sensitivity eligibility, supported kind и task applicability. Restricted item участвует в authority/conflict reasoning, когда trusted-local policy допускает `restricted`; при локальном запрете он fail-closed исключается с `trusted_local_sensitivity_denied`. Это не зависит от capability будущей external/cloud модели и не решает, что ей можно раскрыть.

Registry не определяет composition/precedence. Поэтому несколько одновременно применимых candidates с одинаковыми `kind + subject_ref` из разных scopes не выбираются как winner: они образуют typed `ambiguity`. В ней сохраняются `kind`, `subject_ref`, safe internal authority/provenance linkage и derived sensitivity. Если хотя бы один участник `restricted`, sensitivity ambiguity также `restricted`; automatic declassification отсутствует. Budget применяется только после этой проверки и не может превратить conflict в ложный authority winner. Items с разными `subject_ref` сосуществуют. Порядок eligible детерминирован: kind, updated_at, id; item, который не помещается целиком с authority/provenance metadata, исключается с `budget_exceeded`.

OC-2 не делает downstream disclosure, cloud/local routing, semantic ranking, embeddings или LLM selection и не подключается к Context Assembler, Dialogue или Heart. OC-3 сможет read-only сохранить sensitivity и authority provenance уже retrieved OC в bounded package; OC-5 позднее отвечает за routing/disclosure.
