# ControlGauge — Current Status (session-resume checkpoint)

**This is the single authoritative execution status.** Read it first every session. Other
documents describe plans or decisions; none of them track progress. Navigation:
[MASTER_PLAN](MASTER_PLAN.md) · [BATCH_JOBS](BATCH_JOBS.md) · [EXECUTION_RULES](EXECUTION_RULES.md) ·
[DECISION_LOG](DECISION_LOG.md)

Last updated: 2026-11-02 (Sprint 2 prep; PRs #14 and #15 merged).

## Position

| Item | Value |
|---|---|
| Current sprint | Sprint 2 — Assessment and Database Layer (in progress on `claude/sprint-2-assessment-db`); Sprint 1 exit sign-off (R-01) still pending |
| Active batch | S2-02 (S2-01 implemented/verified, committed on branch) |
| Next action | Continue S2-02 → S2-05 on the same branch, then open the single Sprint 2 PR. Owner: R-01 sign-off still outstanding |
| Baseline | `origin/main` @ `3ed2d67` (merge of PR #15) |
| Merged this session | PR #14 (Continuum planning, `a63f31f`), PR #15 (Graphify guidance, `3ed2d67`) |
| Open PRs | None |

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

- CI on `main` @ `a63f31f`: CI workflow **success**. Graphify PR #15 CI (`fadd9d8`): all 8 checks
  success, including Windows Rust, Windows packaged service and Python 3.12 Windows.
- Local, 2026-11-02, on `claude/sprint-2-prep` from `main` @ `3ed2d67`:
  - `service`: `uv run pytest` → 255 passed; `ruff check` ok; `ruff format --check` ok;
    `mypy` → no issues in 31 files.
  - `app`: `npm test` → 27 passed; `npm run lint`, `npm run build`, `npm run typecheck:test` → exit 0.
- Not run: Rust `cargo` checks locally, Playwright locally, Windows native smoke (local-only by design).

## Repository hygiene (checked 2026-11-02)

- Remote branches with commits not in `main`: `claude/s1-05-frontend-foundation` (`f5216db`
  "updated agents", adds EvidenceGraph-branded `AGENTS.md`). This is not a PR. Owner to decide
  whether to keep or delete it. Agents do not delete branches.
- Local untracked `AGENTS.md` in the working tree is the same file. Owner to decide.
- `claude/s1-01`, `s1-02`, `s1-03`, `sprint-0-bootstrap`, `sprint-0-review-fixes` have no remote
  branch (`gone`). Their PRs were merged. Local cleanup only; no action needed.

## Update log (newest first; keep to one line each)

- 2026-11-02 — Sprint 2 prep: merged PR #14 and #15; verified local tests/lint/build; reconciled
  status (R-00 done).
- 2026-11-02 — Graphify code-graph guidance (PR #15), `--code-only` local use.
- 2026-11-02 — Continuum planning docs created (PR #14).
