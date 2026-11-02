# ControlGauge — Batch Jobs

Small, independently testable units of work. Position lives in [CURRENT_STATUS](CURRENT_STATUS.md);
scope intent in [MASTER_PLAN](MASTER_PLAN.md); workflow in [EXECUTION_RULES](EXECUTION_RULES.md).
Sprint scope/exit criteria stay in `docs/SPRINTS.md`; this file does not replace them.

**Status values:** `Ready` · `Blocked` · `In progress` · `In review` (PR open) · `Done` (merged,
evidence recorded). **Done criteria for every batch** (in addition to its own): relevant tests
run and recorded, docs updated, `CURRENT_STATUS.md` and this file updated, branch + PR per
`CLAUDE.md`, owner merges.

Evidence line format when closing a batch: `Evidence: <commands run → result>; PR #<n>; commit <sha>`.

## Recovery batches (before Sprint 2)

### R-00 — Continuum planning docs
- **Status:** In review (this PR, branch `claude/continuum-planning`).
- **Deliverables:** `docs/planning/*`, `CLAUDE.md` workflow section, stale-status pointers in
  `README.md`, `docs/SPRINTS.md`, `docs/SPRINT_1_BACKLOG.md`.
- **Acceptance:** documentation-only diff; owner reviews and merges.
- **Evidence:** pending (set at PR open).

### R-01 — S1-08 Sprint 1 exit validation
- **Status:** Blocked on owner (human-gated). Depends: R-00 merged (to record result).
- **Scope:** Owner manually runs the full loop on real Windows (launch → service spawn → health
  check → clean shutdown) in dev mode and confirms the S1-09 packaged result; records sign-off.
  Agent drafts a checklist from `docs/SPRINT_1_BACKLOG.md` S1-08 and `docs/SPRINTS.md` Sprint 1
  exit criteria, and records the owner's go/no-go. Agent does not sign off for the owner.
- **Acceptance:** written owner sign-off (or blocker + mitigation) recorded in
  `docs/SPRINT_1_BACKLOG.md` S1-08 and `CURRENT_STATUS.md`; confirm the occupied-port/impersonation
  fail-closed case is covered by the existing tests (cite test names).
- **Validation:** `uv run pytest` (service), `npm test` (app), `cargo test` (src-tauri) or the
  green CI run on the sign-off commit.

### R-02 — Status/doc reconciliation
- **Status:** Blocked on R-01 (can run in parallel in a docs-only PR if owner prefers).
- **Scope:** Update stale "Sprint 0 draft" status lines in `docs/*.md` that are still accurate as
  design but no longer describe project state; update README status; mark S1-01..S1-09 status in
  `SPRINT_1_BACKLOG.md`. No architectural content changes.
- **Acceptance:** one status story across README, SPRINTS, backlog and CURRENT_STATUS.
- **Validation:** grep for stale statuses; docs-only diff.

### R-03 — Resolve Sprint 2 gating questions with owner
- **Status:** Ready (conversation, not code). Depends: none.
- **Scope:** Ask owner for A-2 (DB/model storage location and configurability), and whether
  P-1/P-2 (baseline selection, assessment cloning) are in Sprint 2. Record answers in
  `DECISION_LOG.md` (or ADR in `docs/DECISIONS.md` if architectural) and update `OPEN_QUESTIONS.md`.
- **Acceptance:** A-2 answered or explicitly deferred with a stated interim default approved by owner.

## Sprint 2 — Assessment and Database Layer

Spec: `docs/SPRINTS.md` Sprint 2; schema reference `docs/DATABASE.md`; ADRs D-002, D-005, D-006,
D-016. All batches depend on R-01 and R-03. Any deviation from `DATABASE.md` needs owner
approval. New dependencies (SQLAlchemy, Alembic) require the justification note required by
`CLAUDE.md`; they are already named in the approved stack (D-001) but confirm before adding.

### S2-01 — SQLAlchemy + Alembic foundation
- **Depends:** R-01, R-03. **Status:** Blocked.
- **Deliverables:** SQLite engine/session setup in `service/` at the A-2-approved location;
  Alembic config; empty baseline migration; migration runs on a clean database.
- **Acceptance:** service starts with the DB initialized; migration reproducible on a fresh path;
  DB path never logged with evidence content; nothing bound beyond loopback.
- **Tests:** pytest for engine init and migration-from-scratch; `ruff`, `mypy` clean.
- **Docs:** `service/README.md`, `docs/DATABASE.md` (only if deviating, with owner approval).

### S2-02 — Framework/control schema migration
- **Depends:** S2-01. **Status:** Blocked.
- **Deliverables:** models and migration for `framework`, `control_family` (optional per D-016),
  `control`, `control_enhancement`, `assessment` per `DATABASE.md`.
- **Acceptance:** schema matches `DATABASE.md` (deviations listed); no NIST-specific columns or
  hardcoded NIST values; no framework content inserted (Sprint 3).
- **Tests:** pytest for constraints, FK integrity, upgrade from empty DB.

### S2-03 — Assessment CRUD API
- **Depends:** S2-02. **Status:** Blocked.
- **Deliverables:** authenticated create/list/get endpoints (D-009 token; D-018 identity
  handshake unchanged); input validation; minimal error contract.
- **Acceptance:** endpoints reject missing/invalid credentials; data persists across service
  restarts; assessments require a framework reference (resolved with owner given P-1/P-2).
- **Tests:** pytest for success, validation, unauthorized, persistence across restart.
- **Docs:** `service/README.md` endpoint table.

### S2-04 — Assessment UI (create/list/view)
- **Depends:** S2-03. **Status:** Blocked.
- **Deliverables:** screens replacing the navigation placeholder; service access through the
  existing Rust-mediated channel (frontend never holds the credential).
- **Acceptance:** user can create/list/view an assessment; accessible (focus, labels) consistent
  with S1-05 conventions; empty/error states.
- **Tests:** Vitest component tests; Playwright smoke path.

### S2-05 — Sprint 2 exit validation
- **Depends:** S2-04. **Status:** Blocked.
- **Acceptance:** `SPRINTS.md` Sprint 2 exit criteria demonstrated: create/list/view, persistence
  across app restart (including packaged build via `scripts/package-windows.ps1` if owner
  requests), migration reproducible on clean DB. Owner acceptance recorded; packaged-build
  PyInstaller spec includes any new service dependencies.
- **Tests:** full CI green; manual restart test recorded with evidence.

## Later sprints (milestone level only)

Detailed batches are created at the start of each sprint (PD-002). Do not pre-plan beyond this.

| Sprint | Milestone | First step when reached |
|---|---|---|
| 3 | NIST Rev. 5 catalog loaded, browsable, no hardcoded NIST logic | Resolve C-3 with owner; owner supplies/approves source data |
| 4 | Upload + parsers + verified parser containment (D-022) | Resolve S-1, S-2 |
| 5 | Local GGUF model import, health check, structured output, offline | Owner approves dev model |
| 6 | Hardware detection, catalog, verified download | Resolve P-3, S-4 |
| 7 | Classification with analyst correction | Resolve AI-2 |
| 8 | Validated Mapping Candidates with provenance and run lifecycle | Resolve AI-1; eval sample |
| 9 | Policy mapping assessed against generic pipeline | — |
| 10 | Full review workflow and decision history | Resolve U-3, U-4 |
| 11 | Graph UI | Resolve U-5 |
| 12 | Coverage signals and gap view | Resolve P-1/C-2 |
| 13 | Containment hardening, encryption/retention/SCA decisions | Resolve S-3, S-5, U-6 |
| 14 | Evaluation harness | Resolve AI-3 |
| 15 | Report export | Owner defines format |
| 16 | Release build, PACKAGING.md follow-ups, final acceptance | Resolve A-3 |
