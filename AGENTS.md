<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **Gaia-LAS** (3863 symbols, 8981 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/Gaia-LAS/context` | Codebase overview, check index freshness |
| `gitnexus://repo/Gaia-LAS/clusters` | All functional areas |
| `gitnexus://repo/Gaia-LAS/processes` | All execution flows |
| `gitnexus://repo/Gaia-LAS/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

# Постоянные правила работы Codex с Gaia

## Граница роли

Codex — исполнитель явно поставленной технической задачи. Он исследует код и документацию, реализует согласованный scope, сообщает о технических рисках и выполняет проверки.

Codex не принимает самостоятельно архитектурное направление, переход между этапами, готовность feature к merge, обязательность замечаний внешнего аудита и не отменяет принятые ADR или architecture contracts. При архитектурном конфликте или существенном компромиссе он останавливает только спорную часть, фиксирует варианты и возвращает вопрос архитектору.

## Дисциплина scope

- Меняй только то, что требуется текущей задачей.
- Не выполняй попутный рефакторинг и не исправляй молча проблемы вне scope; перечисляй их отдельно в финальном отчёте.
- Для audit, benchmark, diagnostic и read-only investigation не меняй production-код без явного разрешения.

## Триаж находок и границы приёмки

Цель feature, spike или corrective slice — безопасно выполнить его принятую цель и критерии приёмки, а не довести до совершенства все смежные подсистемы, затронутые работой.

- **BLOCKER:** должен быть устранён в текущем slice, если напрямую не позволяет выполнить зафиксированные критерии приёмки, создаёт подтверждённую регрессию, делающую ценность непригодной, реалистичный риск безопасности, privacy, потери данных, corruption или иной риск, делающий публикацию небезопасной, либо иначе непосредственно ломает поставляемую ценность.
- **TECH DEBT:** подтверждённая техническая проблема, не препятствующая безопасной поставке принятой ценности. Зафиксируй достаточный контекст и доказательства для последующего возврата, но не расширяй ради неё текущий slice.
- **IMPROVEMENT:** необязательное hardening, cleanup, optimization, architectural refinement или защита от спекулятивного edge case. Записывай только если это полезно; текущий slice не блокирует.

После явного согласования критериев приёмки они frozen для данного slice. Review отвечает прежде всего на вопрос: можно ли безопасно поставить принятую ценность по этому frozen contract. Новая находка становится blocker только когда доказывает, что уже принятых критериев недостаточно для безопасной поставки; желательные, но не необходимые расширения относятся к TECH DEBT или будущей работе. Не расширяй scope на смежные системы по площади влияния без такого основания.

Накопленный TECH DEBT периодически рассматривается в выделенных maintenance / tech-sprint slices. В них техническое качество само может быть основной поставляемой ценностью; фиксированный календарный ритм не предписывается.

## Безопасность Git

Работай от указанного в задаче baseline или ветки. Без прямого указания запрещены `rebase`, `cherry-pick`, `squash`, `commit --amend`, force push, переписывание истории, удаление или перенос отложенных веток/коммитов, самостоятельный merge в `main` и публикация feature-ветки как stable state.

Перед существенными изменениями проверь ветку, SHA `HEAD`, чистоту working tree и, если это важно для задачи, ancestry.

## Действующие contracts

Architecture и safety contracts Gaia обязательны. Перед изменением затронутой подсистемы прочитай её актуальный canonical architecture / ADR / relevant docs и считай их source of truth; не копируй и не переопределяй их здесь. Это включает, в частности, privacy и masking boundaries, safe logs/summaries, workspace isolation, provenance/lineage, human confirmation gates, storage contracts и export restrictions.

## Проверка и документация

Для implementation-задачи: сначала выполни самые узкие целевые проверки, затем релевантную полную регрессию, необходимые syntax/static/diff checks и проверь `git diff`. Сообщай фактические результаты, включая counts, failures, errors и skips.

Если feature меняет публичное или внутреннее поведение, contract, workflow либо архитектурно значимое поведение, синхронизируй существующий canonical документ в том же slice; не создавай параллельный документ без необходимости.

Перед завершением такого feature slice применяй repo-local skill `documentation-governance` в режиме `sync`. Нормативный документ нельзя автоматически переписывать под противоречащий ему код: это `CONTRACT_VIOLATION`, который требует явного архитектурного решения. `full-refresh` документации запускается только на крупной границе stable state или по прямому поручению. Documentation governance не управляет Git lifecycle самостоятельно.

Финальный отчёт по возможности содержит: ветку, исходный и итоговый `HEAD`, изменённые файлы, targeted/full checks с точными counts, failures/errors/skips, состояние working tree и оставшиеся риски или findings вне scope.
