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
- **Status:** Done.
- **Deliverables:** `docs/planning/*`, `CLAUDE.md` workflow section, stale-status pointers in
  `README.md`, `docs/SPRINTS.md`, `docs/SPRINT_1_BACKLOG.md`.
- **Acceptance:** documentation-only diff; owner reviews and merges.
- **Evidence:** PR #14 merged as `a63f31f`; CI on `main` success. Docs-only, so no test run required.

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

- **Result (2026-11-02):** Owner accepted S1-08 on the documented evidence (PD-009), recorded in
  `SPRINT_1_BACKLOG.md` S1-08. Fail-closed occupied-port/impersonation coverage: pytest
  `test_port_is_os_assigned_and_unaffected_by_an_unrelated_listener`, cargo
  `fake_http_endpoint_with_wrong_response_is_rejected`,
  `child_death_between_liveness_check_and_send_never_leaks_credential_to_replacement_listener`.
  Done with the Sprint 2 PR merge.

### R-04 — Clean-machine retest of the rebranded installer (release task)
- **Status:** Done (owner-reported, 2026-11-03, PD-014). Owner ran the rebranded executable on a
  clean machine: worked well. Recorded in `docs/PACKAGING.md`; edition, install time and installer
  SHA-256 were not provided. Separate from S1-08 (PD-009).

### R-02 — Status/doc reconciliation
- **Status:** Blocked on R-01 (can run in parallel in a docs-only PR if owner prefers).
- **Scope:** Update stale "Sprint 0 draft" status lines in `docs/*.md` that are still accurate as
  design but no longer describe project state; update README status; mark S1-01..S1-09 status in
  `SPRINT_1_BACKLOG.md`. No architectural content changes.
- **Acceptance:** one status story across README, SPRINTS, backlog and CURRENT_STATUS.
- **Validation:** grep for stale statuses; docs-only diff.

### R-03 — Resolve Sprint 2 gating questions with owner
- **Status:** Answered by owner 2026-11-02 (Sprint 2 approved defaults), recorded as PD-006 and in
  `OPEN_QUESTIONS.md` A-2/P-1/P-2; closes when the Sprint 2 PR merges.
- **Scope:** Ask owner for A-2 (DB/model storage location and configurability), and whether
  P-1/P-2 (baseline selection, assessment cloning) are in Sprint 2. Record answers in
  `DECISION_LOG.md` (or ADR in `docs/DECISIONS.md` if architectural) and update `OPEN_QUESTIONS.md`.
- **Acceptance:** A-2 answered or explicitly deferred with a stated interim default approved by owner.

## Sprint 2 — Assessment and Database Layer

Spec: `docs/SPRINTS.md` Sprint 2; schema reference `docs/DATABASE.md`; ADRs D-002, D-005, D-006,
D-016. Any deviation from `DATABASE.md` needs owner approval. New dependencies (SQLAlchemy,
Alembic) require the justification note required by `CLAUDE.md`; they are already named in the
approved stack (D-001).

**Unblocked by owner instruction (2026-11-02, PD-006/PD-007):** R-03's gating answers were given
as approved defaults (A-2 database location; P-1/P-2 deferred; NIST metadata only), and the owner
directed Sprint 2 to proceed while R-01 (S1-08 sign-off) stays pending. One branch,
`claude/sprint-2-assessment-db`, one PR; batches are "Implemented/verified" until it merges.

### S2-01 — SQLAlchemy + Alembic foundation
- **Depends:** R-03 (answered via PD-006). **Status:** Implemented/verified (on branch, PR pending).
- **Dependency note (`CLAUDE.md`):** `sqlalchemy>=2.0.36,<2.1` (locked 2.0.55) and
  `alembic>=1.14,<2` (locked 1.20.1, pulls Mako/MarkupSafe/greenlet) — the ORM and migration tool
  already named in D-001; no lighter option gives versioned, reproducible SQLite migrations.
- **Result:** `storage.py` (per-user data dir, `CONTROLGAUGE_DATA_DIR` dev/test override, PD-008),
  `db/engine.py` (FK pragma + busy timeout on every connection, real transactions, `BEGIN
  IMMEDIATE` for migrations/writes), `db/migrate.py` + `migrations/` (baseline `0001_baseline`),
  `db/bootstrap.py` (fail-closed init, path never logged). Both entry points migrate before
  opening a socket. PyInstaller script ships migrations as data and rebuilds the project wheel.
- **Issues found and fixed:** (1) frozen build missed `db.models`/`db.base` (only imported by
  data-file `env.py`) → imported from `migrate.py`; (2) parallel children migrating one fresh DB
  raced (`SQLITE_BUSY`) → `BEGIN IMMEDIATE`; (3) local packaging reused a stale cached wheel →
  `--reinstall-package`.
- **Evidence (local, Windows, 2026-11-02):** `uv run pytest` → 276 passed; `ruff check`, `ruff
  format --check`, `mypy` (42 files) clean; frozen exe: `pytest tests/test_supervised_process.py
  --service-exe ...` → 11 passed; `cargo fmt --check`, `cargo clippy -D warnings` clean, `cargo
  test` → 91 passed.
- **Deliverables:** SQLite engine/session setup in `service/` at the A-2-approved location;
  Alembic config; empty baseline migration; migration runs on a clean database.
- **Acceptance:** service starts with the DB initialized; migration reproducible on a fresh path;
  DB path never logged with evidence content; nothing bound beyond loopback.
- **Tests:** pytest for engine init and migration-from-scratch; `ruff`, `mypy` clean.
- **Docs:** `service/README.md`, `docs/DATABASE.md` (only if deviating, with owner approval).

### S2-02 — Framework/control schema migration
- **Depends:** S2-01. **Status:** Implemented/verified (on branch, PR pending).
- **Result:** `db/models.py` + migration `0002_framework_assessment` (5 tables; deviations listed
  in `docs/DATABASE.md` §7 for owner approval — constraints only, incl. the composite
  family/framework FK §2 names as the stricter option). `framework_catalog.py` syncs identity
  metadata from `frameworks/*/framework.json` (untrusted: size-limited, strict schema, invalid
  files skipped); `frameworks/nist-800-53-rev5/framework.json` holds name/version/DOI only. No
  control rows inserted. `assessment_control_scope` not created (P-1 deferred).
- **Evidence (local, 2026-11-02):** `alembic upgrade head` + `alembic check` → "No new upgrade
  operations detected" (migration matches models); downgrade/re-upgrade clean; `uv run pytest` →
  296 passed (new `test_schema.py`: FKs, composite FK, uniqueness, checks, UTC round-trip, no
  control content, AST check that no code string literal names NIST); ruff/mypy clean.
- **Deliverables:** models and migration for `framework`, `control_family` (optional per D-016),
  `control`, `control_enhancement`, `assessment` per `DATABASE.md`.
- **Acceptance:** schema matches `DATABASE.md` (deviations listed); no NIST-specific columns or
  hardcoded NIST values; no framework content inserted (Sprint 3).
- **Tests:** pytest for constraints, FK integrity, upgrade from empty DB.

### S2-03 — Assessment CRUD API
- **Depends:** S2-02. **Status:** Implemented/verified (on branch, PR pending).
- **Result:** `GET /frameworks`, `GET/POST /assessments`, `GET /assessments/{id}` behind the
  unchanged D-009 middleware; strict input validation; sanitized error contract
  (`routes/errors.py`) replacing FastAPI's input-echoing 422; documented in `service/README.md`.
  Assessments require an existing framework (P-1/P-2 deferred per PD-006). DB init failure
  reasons added to the private-channel error allowlist.
- **Issues found and fixed:** unknown-field errors echoed the client's key name → reported
  against `body`.
- **Evidence (local, 2026-11-02):** `uv run pytest` → 342 passed (new `test_assessments_api.py`:
  401 on all routes × 3 bad credentials, validation matrix, no input echo, paging, 404s,
  restart persistence, 503 without DB, sanitized 500 + rollback); ruff/mypy clean. Real process:
  `test_assessment_persists_across_a_real_service_restart` and fail-closed data-dir test pass in
  dev and against the frozen exe (`--service-exe` → 13 passed).
- **Deliverables:** authenticated create/list/get endpoints (D-009 token; D-018 identity
  handshake unchanged); input validation; minimal error contract.
- **Acceptance:** endpoints reject missing/invalid credentials; data persists across service
  restarts; assessments require a framework reference (resolved with owner given P-1/P-2).
- **Tests:** pytest for success, validation, unauthorized, persistence across restart.
- **Docs:** `service/README.md` endpoint table.

### S2-04 — Assessment UI (create/list/view)
- **Depends:** S2-03. **Status:** Implemented/verified (on branch, PR pending).
- **Result:** Rust `supervisor/service_api.rs` + 4 Tauri commands over the pinned connection
  (UUID/paging validation in Rust, 32 KiB request / 2 MiB response bounds, closed schemas, only the
  documented bounded error shape relayed). React: Home/Assessments nav (Settings stays a
  placeholder), list (newest first, "Show more" paging, empty/loading/error+retry, service
  not-ready/failed states), create form (local + service field errors on the field,
  `aria-invalid`/`aria-describedby`, focus to first invalid field, duplicate-submit guard,
  framework preselected with an honest "catalog not loaded" note), detail (404 handled; evidence
  marked not available yet). shadcn `input`/`textarea`/`label`/`native-select` added via
  `npx shadcn add` (no new npm dependency).
- **Evidence (local, 2026-11-02):** `cargo fmt --check`, `cargo clippy -D warnings` clean,
  `cargo test` → 103 passed (real-child round trip incl. validation relay and restart
  persistence; fake-endpoint auth header/fixed path, closed schema, oversized response fails
  closed, oversized request never sent); `npm run lint`, `npm run build`, `npm run typecheck:test`
  exit 0; `npm test` → 42 passed (14 new); `npx playwright test` → 2 passed (new create/view/list
  flow in Chromium). Screens reviewed visually from Chromium screenshots.
- **Deliverables:** screens replacing the navigation placeholder; service access through the
  existing Rust-mediated channel (frontend never holds the credential).
- **Acceptance:** user can create/list/view an assessment; accessible (focus, labels) consistent
  with S1-05 conventions; empty/error states.
- **Tests:** Vitest component tests; Playwright smoke path.

### S2-05 — Sprint 2 exit validation
- **Depends:** S2-04. **Status:** Implemented/verified; **owner acceptance pending** (PR review).
- **Exit criteria (`SPRINTS.md` Sprint 2), agent-verified locally on Windows 11, 2026-11-02:**
  - *Create/list/view:* native test `real app: an assessment created in the UI persists across an
    app restart` drives the real debug app (Tauri + Rust supervisor + Python service) through
    create → detail → list; plus Vitest (42) and Playwright browser flow (2).
  - *Persists across app restarts:* same native test closes the app gracefully, relaunches it,
    and finds and opens the assessment; also `cargo test`
    `assessment_survives_a_supervised_child_restart` and pytest
    `test_assessment_persists_across_a_real_service_restart` (dev and frozen exe).
  - *Migration reproducible on a clean DB:* `test_fresh_directory_is_created_and_migrated_to_head`,
    `test_migration_is_idempotent_and_reproducible`, concurrent first start, `alembic check`
    (no drift), downgrade/upgrade round trip.
  - *Packaged service includes new dependencies:* `scripts/package-windows.ps1 -ServiceOnly` +
    `pytest tests/test_supervised_process.py --service-exe ...` → 13 passed (migrations, models,
    framework metadata all load in the frozen exe). Full NSIS installer and clean-machine install
    not rebuilt/re-run this sprint (not requested); CI's packaged-service job covers the frozen exe.
- **Final local evidence (all on `claude/sprint-2-assessment-db`):** service `uv lock --check`,
  `ruff check`, `ruff format --check`, `mypy` (50 files) clean, `pytest` → 344 passed; Rust
  `cargo fmt --check`, `clippy -D warnings` clean, `cargo test` → 103 passed; app `lint`, `build`,
  `typecheck:test` exit 0, Vitest → 42 passed, Playwright → 2 passed, native → 3 passed.
  No test run touched the real `%LOCALAPPDATA%\ControlGauge` (verified absent afterwards).
- **Still required:** owner review/acceptance of the PR, including the `DATABASE.md` §7 schema
  deviations and PD-008. CI on PR #17 (`d2fc79d`, run 41527916889): all 8 checks pass.
- **Acceptance:** `SPRINTS.md` Sprint 2 exit criteria demonstrated: create/list/view, persistence
  across app restart (including packaged build via `scripts/package-windows.ps1` if owner
  requests), migration reproducible on clean DB. Owner acceptance recorded; packaged-build
  PyInstaller spec includes any new service dependencies.
- **Tests:** full CI green; manual restart test recorded with evidence.

## Sprint 3 — NIST 800-53 Framework Engine

Spec: `docs/SPRINTS.md` Sprint 3; source PD-012 (oscal-content v1.5.0); requirements PD-013;
defaults PD-015 (vendored source, baselines as reference data, one-time pinning of unpinned
assessments). Genericity rule: `ARCHITECTURE.md` §8 (no NIST-specific code). One branch,
`claude/sprint-3-framework-engine`, one PR (same pattern as PD-007).

### S3-01 — Vendored OSCAL source and provenance manifest
- **Depends:** PD-012, PD-015. **Status:** Ready.
- **Deliverables:** upstream catalog + 4 baseline profiles (minified JSON, unmodified) and the
  upstream license under `frameworks/nist-800-53-rev5/oscal/`; `framework.json` extended with the
  catalog/profile manifest (path, SHA-256, release version, tag, commit, URLs, license notice,
  attribution, no-endorsement statement); framework README.
- **Acceptance:** file hashes match the manifest; upstream files byte-identical to the tag.

### S3-02 — Catalog schema (migration 0003)
- **Depends:** S3-01. **Status:** Ready.
- **Deliverables:** `catalog_release` (provenance, one current release per framework), release-scoped
  family/control/enhancement, `control_part`, `control_parameter`, `control_link`,
  `catalog_resource`, `baseline`/`baseline_member`, `assessment.catalog_release_id`; database
  triggers making imported content and an assessment's pinned release immutable.
- **Acceptance:** upgrade from 0002 keeps assessments; `alembic check` clean; downgrade works;
  deviations recorded in `DATABASE.md` §8 for owner review.

### S3-03 — Generic OSCAL importer
- **Depends:** S3-02. **Status:** Ready.
- **Deliverables:** framework-agnostic OSCAL catalog/profile importer (hash check before parse,
  size/count/length/identifier validation, verbatim text, all parts/params/links/back-matter,
  withdrawn status), startup integration, idempotent re-run, refusal to alter an imported release,
  one-time pinning of unpinned assessments, new assessments pinned to the current release.
- **Acceptance:** full catalog loads within the supervisor startup budget; tampered/oversized/
  malformed/unsupported input refused; no NIST literals in code (existing AST test).

### S3-04 — Catalog browsing API
- **Depends:** S3-03. **Status:** Ready.
- **Deliverables:** authenticated read endpoints for a release (provenance, families, baselines),
  control list (family filter, text search, paging), control detail (parts, parameters,
  enhancements, resolved links, references); assessment responses include the pinned release.

### S3-05 — Rust commands and browsing UI
- **Depends:** S3-04. **Status:** Ready.
- **Deliverables:** Tauri commands with closed schemas/bounds; Framework catalog screens
  (families, control list with search, control detail) rendering official text as inert text,
  clearly labeled with source and release; parameter placeholders shown as such.

### S3-06 — Sprint 3 exit validation
- **Depends:** S3-05. **Status:** Ready.
- **Deliverables:** full local test matrix, frozen-service check with the catalog bundled,
  genericity review against `ARCHITECTURE.md` §8, Continuum update, local Graphify refresh
  (`graphify update .`, owner request 2026-11-03).

## Later sprints (milestone level only)

Detailed batches are created at the start of each sprint (PD-002). Do not pre-plan beyond this.

| Sprint | Milestone | First step when reached |
|---|---|---|
| 3 | NIST Rev. 5 catalog loaded, browsable, no hardcoded NIST logic | C-3 resolved (PD-012: oscal-content v1.5.0). Create batches per PD-013: framework-agnostic OSCAL importer with length/identifier validation, verbatim text, full hierarchy/params/references/withdrawn/provenance, framework-version preservation for existing assessments |
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
