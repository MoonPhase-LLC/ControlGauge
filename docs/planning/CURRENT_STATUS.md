# ControlGauge — Current Status (session-resume checkpoint)

**This is the single authoritative execution status.** Read it first every session. Other
documents describe plans or decisions; none of them track progress. Navigation:
[MASTER_PLAN](MASTER_PLAN.md) · [BATCH_JOBS](BATCH_JOBS.md) · [EXECUTION_RULES](EXECUTION_RULES.md) ·
[DECISION_LOG](DECISION_LOG.md)

Last updated: 2026-11-05 (Sprint 4 S4-01..S4-06 implemented/verified; PR opened for owner review).

## Position

| Item | Value |
|---|---|
| Current sprint | Sprint 4 implemented on `claude/sprint-4-evidence-ingestion`; PR #19 in review |
| Active batch | None in progress |
| Next action | Owner: review/merge the Sprint 4 PR, incl. `DATABASE.md` §9 and the PD-016 implementation choices (ctypes launcher, frozen exe as worker, raw IPC upload). After merge: Sprint 5 prep (owner approves a development model) |
| Baseline | `origin/main` @ `9ae472d` (PR #18 merge) |
| Sprint 4 commits | S4-02..S4-04 `97aa67f`, S4-05 `9946ee6`, S4-01/S4-06 docs (final branch commit) |
| Open PRs | #19 (Sprint 4) |

## Verified facts (2026-11-05, this session)

- PDF/DOCX/TXT/CSV evidence uploads in the real app and is parsed inside the parser sandbox
  (per-parse LPAC AppContainer + Job Object, D-027); duplicates are flagged, not re-parsed.
- Containment harness: a hostile probe in the sandbox is denied other evidence, the database,
  writes outside scratch, loopback/internet/DNS and child processes, and is stopped at the memory,
  CPU, wall-clock and output bounds. The frozen exe's `--parse-worker` also runs contained.
- Off Windows, parsing fails closed (`containment_unavailable`).

## Sprint progress

| Sprint | State |
|---|---|
| 0 Architecture/product docs | Complete (PRs #2, #3) |
| 1 Desktop foundation | Complete; S1-08 accepted by owner (PD-009) |
| 2 Assessment and database layer | Complete (PR #17) |
| 3 NIST 800-53 framework engine | Complete (PR #18, merged 2026-11-05, PD-017) |
| 4 Evidence ingestion + parser containment | Implemented/verified; PR in review |
| 5–16 | Not started |

## Blockers and pending decisions

- Owner review: Sprint 4 PR and `DATABASE.md` §9.
- Open: A-2 configurable path / model location, P-1, P-2, C-2; Sprint 5 needs an owner-approved
  development model (`BATCH_JOBS.md` later-sprints table).
- Flaky test to watch: one `cargo test` run in seven had one failure (run took 28 s instead of
  14 s) that did not reproduce in six later runs, including under load; the failing test's name
  was not captured. The new round-trip parse wait was raised from 10 s to 30 s as a precaution.
- Housekeeping for the owner (blocked for the agent by the permission classifier): delete merged
  branches `claude/sprint-2-assessment-db`, `claude/sprint-3-framework-engine` (local and
  `origin`). Left for the owner: `Codex/sprint-0-final-corrections`, `origin/chore/controlgauge-rebrand`
  (merged, other authors), local `claude/sprint-0-review-fixes` (one unmerged commit adding the old
  `AGENTS.md`). The untracked `AGENTS.md` (a current Codex copy of `CLAUDE.md`) is untouched.
- Follow-up candidates: OSCAL part properties (S3-05); re-parse, DOCX headers/footers, OCR (S4-06).

## Last verified test results (local, Windows 11, 2026-11-05)

- `service`: `uv lock --check`, `ruff check`, `ruff format --check`, `mypy` (75 files, also
  `--platform linux`) clean; `pytest` → 575 passed, 6 skipped; frozen exe supervised suite +
  frozen worker → 17 passed.
- `app/src-tauri`: `cargo fmt --check`, `cargo clippy -D warnings` clean; `cargo test` → 112 passed.
- `app`: `npm run lint`, `typecheck:test`, `build` exit 0; Vitest → 61 passed; Playwright
  browser → 2 passed; native Windows smoke (`test:e2e:native`) → 5 passed.
- CI: pending on PR #19.
- Graphify: `graphify update .` (AST only, local) → 2,518 nodes, 6,130 edges.

## Update log (newest first; keep to one line each)

- 2026-11-05 — Sprint 4 S4-01..S4-06 implemented/verified on one branch; graph refreshed; PR #19 opened.
- 2026-11-05 — PR #18 merged by owner instruction (PD-017); S-1/S-2 resolved (PD-016, D-027).
- 2026-11-03 — Sprint 3 S3-01..S3-06 implemented/verified on one branch; graph refreshed; PR #18 opened.
- 2026-11-03 — PD-014 (housekeeping, R-04 passed) and PD-015 (Sprint 3 defaults) recorded.
- 2026-11-02 — Owner decisions PD-009..PD-013 recorded; PD-011 safeguards added; PR #17 merged by owner instruction.
- 2026-11-02 — Sprint 2 S2-01..S2-05 implemented/verified on one branch; PR opened for review.
- 2026-11-02 — Sprint 2 prep: merged PR #14 and #15; verified local tests/lint/build (R-00 done).
- 2026-11-02 — Graphify code-graph guidance (PR #15), `--code-only` local use.
- 2026-11-02 — Continuum planning docs created (PR #14).
