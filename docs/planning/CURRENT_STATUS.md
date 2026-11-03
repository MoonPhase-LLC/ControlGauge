# ControlGauge — Current Status (session-resume checkpoint)

**This is the single authoritative execution status.** Read it first every session. Other
documents describe plans or decisions; none of them track progress. Navigation:
[MASTER_PLAN](MASTER_PLAN.md) · [BATCH_JOBS](BATCH_JOBS.md) · [EXECUTION_RULES](EXECUTION_RULES.md) ·
[DECISION_LOG](DECISION_LOG.md)

Last updated: 2026-11-03 (Sprint 3 S3-01..S3-06 implemented/verified; PR opened for owner review).

## Position

| Item | Value |
|---|---|
| Current sprint | Sprint 3 implemented on `claude/sprint-3-framework-engine`; PR in review |
| Active batch | None in progress |
| Next action | Owner: review/merge the Sprint 3 PR, including `DATABASE.md` §8 schema deviations. After merge: Sprint 4 prep (resolve S-1, S-2) |
| Baseline | `origin/main` @ `93c157c` (PR #17 merge) |
| Sprint 3 commits | planning `b2f8865`, S3-01 `c53a905`, S3-02/03 `cd8082a`, S3-04 `41f96ab`, S3-05 `6b3bd3b`, S3-06 (final branch commit) |
| Open PRs | Sprint 3 PR (see `BATCH_JOBS.md` S3-06) |

## Verified facts (2026-11-03, this session)

- The shipped NIST SP 800-53 Release 5.2.0 catalog (oscal-content v1.5.0) imports at startup,
  hash-verified, and is stored verbatim (round-trip test over all 1,196 controls/enhancements).
- It is browsable in the real app (native test) and imports from scratch in the frozen service
  exe (`--service-exe` suite → 13 passed).
- New assessments are pinned to the current release; a pinned release cannot change (DB trigger).

## Sprint progress

| Sprint | State |
|---|---|
| 0 Architecture/product docs | Complete (PRs #2, #3) |
| 1 Desktop foundation | Complete; S1-08 accepted by owner (PD-009) |
| 2 Assessment and database layer | Complete (PR #17) |
| 3 NIST 800-53 framework engine | Implemented/verified; PR in review |
| 4–16 | Not started |

## Blockers and pending decisions

- Owner review: Sprint 3 PR and `DATABASE.md` §8 (release-scoped catalog schema; `official_text`
  replaced by `control_part`).
- Open: A-2 configurable path / model location, P-1, P-2, C-2; S-1, S-2 needed for Sprint 4.
- Follow-up candidate: store OSCAL part properties (distinguishes SP 800-53A assessment-method
  parts in the UI); see `BATCH_JOBS.md` S3-05.
- Done this session (PD-014): stale `AGENTS.md` deleted; `claude/s1-05-frontend-foundation` was
  already absent on `origin`; R-04 clean-machine retest reported passed by the owner.

## Last verified test results (local, Windows 11, 2026-11-03)

- `service`: `uv lock --check`, `ruff check`, `ruff format --check`, `mypy` (58 files) clean;
  `pytest` → 460 passed, 1 skipped (symlink test; runs on Linux CI); frozen exe supervised suite
  → 13 passed.
- `app/src-tauri`: `cargo fmt --check`, `cargo clippy -D warnings` clean; `cargo test` → 108 passed.
- `app`: `npm run lint`, `typecheck:test`, `build` exit 0; Vitest → 51 passed; Playwright
  browser → 2 passed; native Windows smoke (`test:e2e:native`) → 4 passed.
- CI: pending on the Sprint 3 PR.
- Graphify: `graphify update .` (AST only) → 2,099 nodes, 4,965 edges.

## Update log (newest first; keep to one line each)

- 2026-11-03 — Sprint 3 S3-01..S3-06 implemented/verified on one branch; graph refreshed; PR opened.
- 2026-11-03 — PD-014 (housekeeping, R-04 passed) and PD-015 (Sprint 3 defaults) recorded.
- 2026-11-02 — Owner decisions PD-009..PD-013 recorded; PD-011 safeguards added; PR #17 merged by owner instruction.
- 2026-11-02 — Sprint 2 S2-01..S2-05 implemented/verified on one branch; PR opened for review.
- 2026-11-02 — Sprint 2 prep: merged PR #14 and #15; verified local tests/lint/build (R-00 done).
- 2026-11-02 — Graphify code-graph guidance (PR #15), `--code-only` local use.
- 2026-11-02 — Continuum planning docs created (PR #14).
