# ControlGauge — Current Status (session-resume checkpoint)

**This is the single authoritative execution status.** Read it first every session. Other
documents describe plans or decisions; none of them track progress. Navigation:
[MASTER_PLAN](MASTER_PLAN.md) · [BATCH_JOBS](BATCH_JOBS.md) · [EXECUTION_RULES](EXECUTION_RULES.md) ·
[DECISION_LOG](DECISION_LOG.md)

Last updated: 2026-11-02 (Continuum setup; docs-only, branch `claude/continuum-planning`).

## Position

| Item | Value |
|---|---|
| Current sprint | Sprint 1 — Desktop Foundation (implementation done, exit sign-off pending) |
| Active batch | None in progress |
| Next action | **R-01** (see below), then **R-02**; Sprint 2 batches are blocked on R-02 |
| Baseline | `origin/main` @ `146cea9` (merge of PR #13) |
| Continuum PR | Not yet opened/merged until owner reviews (see BATCH_JOBS `R-00`) |

## Verified facts (checked against repo/GitHub on 2026-11-02)

- PRs #1–#13 are all merged (`gh pr list`): Sprint 0 (#2, #3), S1-01 (#4), S1-02 (#5),
  S1-03 (#6), S1-04 (#7), S1-05 (#8), S1-06 (#9), S1-07 (#10), S1-09 (#11), rebrand (#12, #13).
- CI on `main` for `146cea9`: run 39857763377 completed **success** (2026-10-22). This is the only
  test evidence verified this session. No tests were run locally during Continuum setup.
- Code present: `app/` (Tauri v2 + React/TS shell, supervisor in `app/src-tauri/`, Vitest and
  Playwright tests), `service/` (FastAPI, `/health` and challenge routes, 15 pytest modules),
  `scripts/` (Windows packaging and diagnostics), `.github/workflows/ci.yml`.
- Absent from the repo (so Sprints 2–16 are unimplemented): database/SQLAlchemy/Alembic,
  evidence upload/parsers, framework data in `frameworks/` (placeholder only), model runtime,
  AI pipeline, assessment, review, graph, gap, report features.
- 2026-10-22: project rebranded EvidenceGraph → ControlGauge (PRs #12, #13); migration notes in
  `docs/PACKAGING.md`.
- 2026-10-07: S1-09 packaged-build spike merged (PR #11). Per the product owner's manual test on a
  clean Windows machine (recorded in `docs/DECISIONS.md` D-001 and `docs/PACKAGING.md`), it
  passed. Windows edition, install time and installer hash were not recorded.

## Historical claims (recorded in docs, not re-verified here)

- The S1-09 clean-machine pass is the owner's manual result; it is not reproducible from CI.
- Per-ticket S1-01..S1-06 acceptance was reviewed in their PRs; not re-audited in Continuum setup.

## Sprint progress

| Sprint | State |
|---|---|
| 0 Architecture/product docs | Complete (docs merged, PRs #2, #3). Its open questions remain open by design |
| 1 Desktop foundation | S1-01..S1-07 and S1-09 merged. **S1-08 (exit validation / owner sign-off) not done** |
| 2–16 | Not started. No repository evidence of implementation |

## Blockers and pending decisions

- **S1-08 sign-off** (owner): confirm Sprint 1 exit criteria, including the dev-mode loop on real
  Windows and the S1-09 result. Blocks Sprint 2 per `docs/SPRINT_1_BACKLOG.md`.
- Open product/security questions are unchanged: see `docs/OPEN_QUESTIONS.md`. Those gating Sprint 2
  or 3: **A-2** (DB/model storage location), **C-3** (authoritative NIST source/licensing),
  **P-1/P-2** (baseline selection, assessment cloning). Do not invent answers.
- Untracked `AGENTS.md` in the working tree is a stale, EvidenceGraph-branded Codex instruction file.
  Not part of this work; owner to decide whether to commit, rename or discard.

## Last verified test results

CI (`.github/workflows/ci.yml`, frontend + browser smoke + service + Rust) green on `main`
@ `146cea9`. Windows native smoke is local-only by design (`CONTRIBUTING.md`) and was not run.

## Update log (newest first; keep to one line each)

- 2026-11-02 — Continuum planning docs created (docs-only). No code run or changed.
