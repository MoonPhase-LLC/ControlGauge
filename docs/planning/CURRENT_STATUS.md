# ControlGauge — Current Status (session-resume checkpoint)

**This is the single authoritative execution status.** Read it first every session. Other
documents describe plans or decisions; none of them track progress. Navigation:
[MASTER_PLAN](MASTER_PLAN.md) · [BATCH_JOBS](BATCH_JOBS.md) · [EXECUTION_RULES](EXECUTION_RULES.md) ·
[DECISION_LOG](DECISION_LOG.md)

Last updated: 2026-11-02 (owner decisions PD-009..PD-013; Sprint 2 PR #17 merged by owner instruction).

## Position

| Item | Value |
|---|---|
| Current sprint | Sprint 2 complete (PR #17 merged); Sprint 3 next |
| Active batch | None in progress |
| Next action | Sprint 3 prep: create batches per PD-012/PD-013 (OSCAL importer for oscal-content v1.5.0). Owner: R-04 clean-machine retest of the rebranded installer (release task) |
| Baseline | `origin/main` after the PR #17 merge |
| Sprint 2 commits | S2-01 `3cfc00a`, S2-02 `ed3b5a8`, S2-03 `b6e93be`, S2-04 `ddd0989`, S2-05 `d2fc79d`, owner decisions + PD-011 safeguards (final branch commit) |
| Open PRs | None expected after the merge |

## Verified facts (2026-11-02, this session)

- Sprint 2 exit criteria demonstrated locally on Windows 11, including the native test that
  creates an assessment in the real app, restarts the app, and finds it (BATCH_JOBS S2-05).
- The frozen PyInstaller service loads the new dependencies, migrations and framework metadata
  (`--service-exe` supervised suite → 13 passed).
- PR #17 CI @ `d2fc79d` (run 41527916889): all 8 checks passed.

## Sprint progress

| Sprint | State |
|---|---|
| 0 Architecture/product docs | Complete (PRs #2, #3) |
| 1 Desktop foundation | Complete; S1-08 accepted by owner (PD-009) |
| 2 Assessment and database layer | Complete (PR #17) |
| 3–16 | Not started |

## Blockers and pending decisions

- Owner decisions 2026-11-02: S1-08 accepted (PD-009), schema §7 approved (PD-010), PD-008
  approved with network-path safeguards (PD-011, implemented and tested), C-3 resolved (PD-012),
  Sprint 3 requirements (PD-013). Owner instructed the agent to merge PR #17.
- Open: R-04 clean-machine retest (release task); A-2 configurable path / model location, P-1,
  P-2, C-2.
- Untracked `AGENTS.md` (stale EvidenceGraph Codex file) and remote branch
  `claude/s1-05-frontend-foundation` — owner to decide; agents do not delete them.

## Last verified test results (local, Windows 11, 2026-11-02)

- `service`: `ruff check`, `ruff format --check`, `mypy` (50 files) clean; `pytest` → 352 passed
  (after PD-011 safeguards); frozen exe supervised suite → 13 passed (before PD-011).
- `app/src-tauri`: `cargo fmt --check`, `cargo clippy -D warnings` clean; `cargo test` → 103 passed.
- `app`: `npm run lint`, `build`, `typecheck:test` exit 0; Vitest → 42 passed; Playwright
  browser → 2 passed; native Windows smoke (`test:e2e:native`) → 3 passed.
- CI: PR #17 @ `d2fc79d` all 8 checks passed; the final PD-011 commit's CI is checked before merge.

## Update log (newest first; keep to one line each)

- 2026-11-02 — Owner decisions PD-009..PD-013 recorded; PD-011 safeguards added; PR #17 merged by owner instruction.
- 2026-11-02 — Sprint 2 S2-01..S2-05 implemented/verified on one branch; PR opened for review.
- 2026-11-02 — Sprint 2 prep: merged PR #14 and #15; verified local tests/lint/build (R-00 done).
- 2026-11-02 — Graphify code-graph guidance (PR #15), `--code-only` local use.
- 2026-11-02 — Continuum planning docs created (PR #14).
