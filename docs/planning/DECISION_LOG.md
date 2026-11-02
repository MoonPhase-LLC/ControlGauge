# Continuum Decision Log

Execution and planning decisions only. Architectural decisions live in
[`docs/DECISIONS.md`](../DECISIONS.md) (D-001..D-026) and are not duplicated or overridden here;
product/security questions awaiting the owner live in [`docs/OPEN_QUESTIONS.md`](../OPEN_QUESTIONS.md).
Navigation: [CURRENT_STATUS](CURRENT_STATUS.md) · [MASTER_PLAN](MASTER_PLAN.md) ·
[BATCH_JOBS](BATCH_JOBS.md) · [EXECUTION_RULES](EXECUTION_RULES.md)

Format: `PD-NNN — date — decision — rationale — who decided`. Append only; supersede with a new entry.

| ID | Date | Decision | Rationale | Decided by |
|---|---|---|---|---|
| PD-001 | 2026-11-02 | `docs/planning/CURRENT_STATUS.md` is the only execution-status tracker; `SPRINTS.md` stays the roadmap, `SPRINT_1_BACKLOG.md` stays ticket specs | One source of truth avoids conflicting progress claims | Owner (Continuum request) |
| PD-002 | 2026-11-02 | Sprint 2–16 work is split into batches only as a sprint nears execution; later sprints stay milestone-level | Matches `SPRINTS.md` note that later backlogs are created at sprint start | Owner (Continuum request) |
| PD-003 | 2026-11-02 | Add recovery batches R-01 (S1-08 sign-off) and R-02 (doc/status reconciliation) before Sprint 2 | S1-08 is a documented gate for Sprint 2; several docs still say "Sprint 0 draft" | Proposed by agent; owner to confirm |
| PD-005 | 2026-11-02 | Graphify is used per developer, locally, only in `--code-only` / `update` mode; LLM-backed commands, hosted MCP, `clone`/`add`/`--push`, and `claude install`/`hook install` are not used | Keeps code and evidence off external services (`CLAUDE.md` privacy rules); `graphify-out/` is gitignored | Owner (PR #15 approved for merge) |
| PD-004 | 2026-11-02 | Batch branches use `claude/<short-description>` per `CLAUDE.md`; Continuum bookkeeping updates ship in the same PR as the batch work | Keeps status in step with merged code | Existing rule applied |

## Not decided here

Everything in `docs/OPEN_QUESTIONS.md` (P-, C-, U-, A-, AI-, S- items) remains open. Sprint 2
cannot finish without owner input on **A-2** (storage location); Sprint 3 on **C-3** (NIST source).
