# ControlGauge — Current Status (session-resume checkpoint)

**This is the single authoritative execution status.** Read it first every session. Other
documents describe plans or decisions; none of them track progress. Navigation:
[MASTER_PLAN](MASTER_PLAN.md) · [BATCH_JOBS](BATCH_JOBS.md) · [EXECUTION_RULES](EXECUTION_RULES.md) ·
[DECISION_LOG](DECISION_LOG.md)

Last updated: 2026-11-02 (Sprint 2 implemented on branch; PR opened for owner review).

## Position

| Item | Value |
|---|---|
| Current sprint | Sprint 2 — Assessment and Database Layer: **implemented/verified, in review** |
| Active batch | None in progress. S2-01..S2-05 implemented/verified on `claude/sprint-2-assessment-db` |
| Next action | **Owner:** review and merge the Sprint 2 PR (incl. `DATABASE.md` §7 deviations, PD-008); give S1-08 sign-off (R-01). **Agent, after merge:** mark S2-* and R-03 Done with the merge SHA, then start Sprint 3 prep (C-3 first) |
| Baseline | `origin/main` @ `4699152` (merge of PR #16) |
| Branch | `claude/sprint-2-assessment-db`: S2-01 `3cfc00a`, S2-02 `ed3b5a8`, S2-03 `b6e93be`, S2-04 `ddd0989`, S2-05 (this commit) |
| Open PRs | #17 (Sprint 2), CI green |

## Verified facts (2026-11-02, this session)

- Owner approved Sprint 2 defaults and directed execution with S1-08 still pending (PD-006, PD-007).
- Sprint 2 exit criteria demonstrated locally on Windows 11, including the native test that
  creates an assessment in the real app, restarts the app, and finds it (BATCH_JOBS S2-05).
- The frozen PyInstaller service loads the new dependencies, migrations and framework metadata
  (`--service-exe` supervised suite → 13 passed).
- PRs #1–#16 merged; no other open PRs at session start.

## Sprint progress

| Sprint | State |
|---|---|
| 0 Architecture/product docs | Complete (PRs #2, #3) |
| 1 Desktop foundation | S1-01..S1-07, S1-09 merged. **S1-08 owner sign-off still pending (R-01)** |
| 2 Assessment and database layer | Implemented/verified on branch; PR in review; Done on merge |
| 3–16 | Not started |

## Blockers and pending decisions

- **R-01 / S1-08 sign-off** (owner) — unchanged; Sprint 2 proceeded by owner instruction (PD-007).
- **Sprint 2 PR review** (owner): approve the constraint-only schema deviations in
  `docs/DATABASE.md` §7 and the dev/test-only `CONTROLGAUGE_DATA_DIR` override (PD-008).
- **Sprint 3 gate:** C-3 (authoritative NIST source and licensing) must be answered before any
  control catalog is loaded. Still open: A-2 configurable path / model location, P-1, P-2, C-2.
- Untracked `AGENTS.md` (stale EvidenceGraph Codex file) and remote branch
  `claude/s1-05-frontend-foundation` — owner to decide; agents do not delete them.

## Last verified test results (local, Windows 11, branch head, 2026-11-02)

- `service`: `uv lock --check` ok; `ruff check`, `ruff format --check`, `mypy` (50 files) clean;
  `pytest` → 344 passed; frozen exe supervised suite → 13 passed.
- `app/src-tauri`: `cargo fmt --check`, `cargo clippy -D warnings` clean; `cargo test` → 103 passed.
- `app`: `npm run lint`, `build`, `typecheck:test` exit 0; Vitest → 42 passed; Playwright
  browser → 2 passed; native Windows smoke (`test:e2e:native`) → 3 passed.
- CI on PR #17 @ `d2fc79d`: run 41527916889 — all 8 checks **pass** (frontend, browser smoke,
  Python 3.11/3.12 ubuntu + 3.12 Windows, Rust Windows, packaged service Windows, claude-review).

## Update log (newest first; keep to one line each)

- 2026-11-02 — Sprint 2 S2-01..S2-05 implemented/verified on one branch; PR opened for review.
- 2026-11-02 — Sprint 2 prep: merged PR #14 and #15; verified local tests/lint/build (R-00 done).
- 2026-11-02 — Graphify code-graph guidance (PR #15), `--code-only` local use.
- 2026-11-02 — Continuum planning docs created (PR #14).
