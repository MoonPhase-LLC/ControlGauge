# ControlGauge — Current Status (session-resume checkpoint)

**This is the single authoritative execution status.** Read it first every session. Other
documents describe plans or decisions; none of them track progress. Navigation:
[MASTER_PLAN](MASTER_PLAN.md) · [BATCH_JOBS](BATCH_JOBS.md) · [EXECUTION_RULES](EXECUTION_RULES.md) ·
[DECISION_LOG](DECISION_LOG.md)

Last updated: 2026-11-25 (Sprint 6 shipped @ `cd5d569`; owner authorized Sprint 6.1 — cross-platform validation & dev-infra hardening).

## Position

| Item | Value |
|---|---|
| Current sprint | Sprint 6.1 (cross-platform validation and development infrastructure hardening) — owner-issued scope 2026-11-25, authorized through implementation + §7 merge; NOT Sprint 7 |
| Active batch | S6.1-01 kickoff docs implemented; S6.1-02..S6.1-08 ready (see `BATCH_JOBS.md` Sprint 6.1) |
| Next action | S6.1-02 CI workflow hardening (branch-push triggers + CI Gate) → S6.1-03 dispatch seams + parameterized cross-platform tests → audit (04) → validate command (05) → governance (06) → demonstration (07) → reviewer + security gate (08); branch-protection application + agent PR creation stay BLOCKED pending owner PAT category grants (AI-2 package) |
| Baseline | `origin/main` @ `cd5d569` (Sprint 6 merge `0bd56db` + Windows fix-forward 4 `58e426c`/`cd5d569`; §7) |
| Sprint 5 commits | Kickoff docs `b4451e8`; S5-01 `58a2fa1`, S5-02 `ceea877`, S5-03 `d62fff1`, S5-04 `d7c0ba8`, S5-05 `f163967`, S5-06 `5959a4c`, S5-07 `d97cd58`; review fix round `5934465`; merge `5925928`; CI fix-forwards `e1ff803`/`e55a1be` and `6945871`/`fd9d356` |
| Open PRs | None |

## Verified facts (2026-11-05 Sprint 4; 2026-11-16 Sprint 4.5; 2026-11-17 Sprint 5)

- PDF/DOCX/TXT/CSV evidence uploads in the real app and is parsed inside the parser sandbox
  (per-parse LPAC AppContainer + Job Object, D-027); duplicates are flagged, not re-parsed.
- Containment harness: a hostile probe in the sandbox is denied other evidence, the database,
  writes outside scratch, loopback/internet/DNS and child processes, and is stopped at the memory,
  CPU, wall-clock and output bounds. The frozen exe's `--parse-worker` also runs contained.
- Linux parser sandbox (D-028): hostile probes denied as on Windows; real worker parses all four
  formats contained; bwrap/AppArmor prerequisite documented.
- On platforms with no sandbox (macOS/other), parsing fails closed (`containment_unavailable`);
  on Linux, a missing `bwrap`/AppArmor profile fails closed the same way (D-028).
- Model runtime (Sprint 5): llama.cpp `llama-server` b12480 runs as a supervised subprocess per
  capability with a fresh per-launch credential (D-017 via `LLAMA_API_KEY` child env, never argv);
  `/health` is auth-exempt, all other endpoints 401 anonymous; split GGUFs load from shard 1;
  nomic embeddings are 768-dim, finite, deterministic. b12480's `json_schema` grammar enforces
  SHAPE, not numeric ranges — the deterministic validator owns ranges (DEV_MODELS.md §5.1).
  Baselines (development only): 7B generation ~46.5 tok/s warm CPU (7.9 GiB child RSS),
  embedding 3.9 ms (158 MiB RSS). `GET /models` is structural (no live child contact).
- Sprint 6 (2026-11-22/23, dev box): hardware detection answers with real machine facts
  (Linux: /proc/meminfo + nvidia-smi — this box's 3× RTX PRO 6000 enumerate with ≈95.6 GiB
  each; Windows DXGI path fixture-verified only); the curated catalog serves the four PD-026
  entries with immutable revision-pinned URLs + per-file SHA-256s; the real 84 MB nomic
  download verified against the manifest pin end to end (fail-closed checksum, resumable,
  approval-gated); recommendations are pure §5 math — detected hardware and all three
  profiles answer closed-vocabulary verdicts, with the no-ceiling property (full catalog
  recommended on enterprise-288gb) and honest consumer-cpu tiering (14B infeasible);
  the user-managed registry distinguishes origin/verification end to end (migration 0006),
  external loopback endpoints register probe-gated/attach/generate/detach (real-engine
  round trip proven in S6-05); the exit-validation flow composes it all offline
  (test_integration_sprint6_exit_flow.py, DEV_MODELS.md §9).

## Sprint progress

| Sprint | State |
|---|---|
| 0 Architecture/product docs | Complete (PRs #2, #3) |
| 1 Desktop foundation | Complete; S1-08 accepted by owner (PD-009) |
| 2 Assessment and database layer | Complete (PR #17) |
| 3 NIST 800-53 framework engine | Complete (PR #18, merged 2026-11-05, PD-017) |
| 4 Evidence ingestion + parser containment | Complete (PR #19, merged 2026-11-05, PD-018) |
| 4.5 Linux parser containment parity | Complete (merged to `main` 2026-11-17, PD-022) |
| 5 Local model runtime | Complete (merged to `main` 2026-11-17, PD-025; manual UI acceptance run still operator-gated) |
| 6 Hardware detection + model catalog/recommendations | Complete (merged to `main` 2026-11-23 @ `cd5d569`, §7, reviewer APPROVE; CI green 7/7 post-fix-forward-4; manual UI acceptance run still operator-gated) |
| 6.1 Cross-platform validation + dev-infra hardening | In progress (owner-authorized 2026-11-25; `claude/sprint-6-1-validation-hardening`; S6.1-01 done, 02–08 ready) |
| 7–16 | Not started (Sprint 7 = artifact classification, gated on owner decision AI-2) |

## Blockers and pending decisions

- Open: A-2 configurable path / model location, P-1, P-2, C-2.
- Resolved 2026-11-17 (PD-023): Sprint 5 dev models — Qwen2.5-7B-Instruct Q4_K_M (generation) +
  nomic-embed-text-v1.5 Q4_K_M (embedding), development/validation only; Sprint 6 curates
  production options independently. Mechanism default PD-024 (llama-server subprocess + HTTP).
- Resolved 2026-11-17 (PD-025): Sprint 5 merged to `main` under §7 after reviewer APPROVE
  (fix round `5934465` closed F1/F2+N1/N2; gates re-run green).
- Sprint 5 open item (reviewer ruling, not a merge blocker): manual UI acceptance run —
  import → start → health → stop of the real Qwen model from the built app — required before
  calling the sprint "shipped"; operator-gated.
- Resolved 2026-11-22 (PD-026): Sprint 6 gate — Qwen2.5 1.5B/7B/14B tiers + nomic catalog approved
  (a baseline, not a capability ceiling); official HF revision-pinned + SHA-256-verified downloads;
  universal local model support (curated catalog + separate user-managed registry, backend
  abstraction, external local endpoints); high-VRAM-first hardware targets (32 GB VRAM and 288 GB
  aggregate-VRAM multi-GPU systems primary, consumer secondary); hallucination minimization is the
  program's highest priority (evidence grounding/abstention/verification documented as Sprint 6
  integration points, built in later sprints).
- CI fix-forward on the Sprint 5 merge (2026-11-22, `e55a1be`): httpx frozen-bundle classification
  (PyInstaller job) + a Unix-biased Rust path-validation test fixed; the third red job (Service
  windows-latest) was a GitHub Actions runner disk-full infrastructure failure (check-run
  annotation, no failed step) — re-verdicted by the fix-branch CI push.
- CI fix-forward 2 (2026-11-23, `fd9d356`): the windows-latest pytest death was NOT pure infra —
  `test_import_rejects_oversized` materialized a 256 GiB "sparse" fixture via `truncate()`, free on
  Linux but disk-consuming on Windows/NTFS, killing the runner mid-suite with ENOSPC (no failed
  step, no log blob). Fixed by injecting the oversized size through the `_read_head_and_size` seam
  (all platforms) + a POSIX-only real-sparse companion test; nothing weakened, Windows gains
  oversized-refusal coverage. Lesson: size-bound test fixtures must never rely on truncate() holes
  cross-platform.
- Sprint 6 fold-ins from the Sprint 5 review (non-blocking): import route checks extension before
  existence (folded into S6-04); N5 `manager.status()` lock scope during live checks (folded into
  S6-05); D-028 follow-ups where they fit (Linux packaging script, CI Linux rust job, rust-toolchain
  linux target).
- Flaky test to watch: one `cargo test` run in seven had one failure (run took 28 s instead of
  14 s) that did not reproduce in six later runs, including under load; the failing test's name
  was not captured. The new round-trip parse wait was raised from 10 s to 30 s as a precaution.
- Housekeeping (owner-instructed 2026-11-16, PD-019): the five merged branches
  `claude/sprint-2-assessment-db`, `claude/sprint-3-framework-engine`, `claude/sprint-4-evidence-ingestion`,
  `Codex/sprint-0-final-corrections`, `chore/controlgauge-rebrand` were deleted from `origin` (merge
  status verified via `git branch --merged origin/main` first), using an owner-provided PAT after the
  stored fine-grained PAT (Xirro9) returned 403 for this repo. `origin/claude/record-sprint-4-merge` is
  kept (unmerged docs). `claude/sprint-0-review-fixes` (one unmerged commit, old `AGENTS.md`) exists
  only on the owner's Windows machine.
- Follow-up candidates: OSCAL part properties (S3-05); re-parse, DOCX headers/footers, OCR (S4-06).

## Last verified test results (local, Windows 11, 2026-11-05)

- `service`: `uv lock --check`, `ruff check`, `ruff format --check`, `mypy` (75 files, also
  `--platform linux`) clean; `pytest` → 575 passed, 6 skipped; frozen exe supervised suite +
  frozen worker → 17 passed.

## Last verified on Linux (2026-11-16, compatibility audit + Sprint 4.5)

- `service` (Sprint 4.5, bwrap 0.9.0): `uv run pytest -q` → **578 passed, 7 skipped** in 44 s —
  the D-022 containment harness now runs contained on Linux (bubblewrap, D-028: probes denied,
  real worker parses all four formats, bounds enforced). Remaining skips: 5 Windows-only
  containment tests, the `GetDriveTypeW` probe, and the fail-closed test (skips because a sandbox
  *is* available here). `ruff check`, `ruff format --check`, `mypy` (77 files) clean. Windows
  invariant unchanged: **575 passed, 6 skipped**. (Pre-Sprint-4.5 audit baseline: 561 passed,
  20 skipped.)
- `app/src-tauri`: `cargo check --all-targets` on Linux → **Finished**, zero errors; one benign
  dead-code warning (`JobObjectCreateFailed`/`JobObjectAssignFailed` never constructed off Windows).
  The supervisor already has non-Windows dev/production resolution paths (`bin/python`,
  `controlgauge-service` without `.exe`).
- `app/src-tauri`: `cargo fmt --check`, `cargo clippy -D warnings` clean; `cargo test` → 112 passed.
- `app`: `npm run lint`, `typecheck:test`, `build` exit 0; Vitest → 61 passed; Playwright
  browser → 2 passed; native Windows smoke (`test:e2e:native`) → 5 passed.
- CI on PR #19 (run 42120874860): all checks pass; Windows service job 576 passed, 5 skipped (containment harness ran, not skipped); frozen worker in the sandbox 4 passed; supervised frozen suite 13 passed.
- Graphify: `graphify update .` (AST only, local) → 2,518 nodes, 6,130 edges.

## Last verified on Linux (2026-11-23, Sprint 6 batches)

- `service`: ruff + `ruff format --check` clean; mypy strict clean (136 files) on linux AND
  `--platform win32`; `pytest -q` → **936 passed, 36 skipped** offline (the Windows-invariant
  pytest suite grows only on main-push CI; the 3 new skips are the env-gated S6-08 module).
- Env-gated live (dev gates set): `test_integration_sprint6_exit_flow.py` **3/3 PASSED**
  (1.81 s; evidence in DEV_MODELS §9) and `test_download_integration.py` **1/1 PASSED**
  (16.92 s, real 84 MB nomic download from the pinned HF revision).
- Rust/TS untouched by S6-08 — S6-07 baselines stand: cargo **112p/8f** (documented failing
  set), vitest **85/85**, Playwright **4/4**, `npm run build` exit 0.

## Update log (newest first; keep to one line each)

- 2026-11-25 — Sprint 6.1 authorized by owner (cross-platform validation + dev-infra hardening, ff3/ff4 root causes); investigation complete (root cause: origin PAT lacks fine-grained PR-write + Administration categories → §7 merges bypass the PR path → only push:[main] CI ever fired; ci.yml has had PR triggers since S1-07); kickoff docs + architect spec done; Sprint 7 NOT started.
- 2026-11-23 — Sprint 6 SHIPPED under §7: reviewer APPROVE, fix round `a746ba5` (N1–N4), merge `0bd56db`; CI's first Windows run of Sprint 6 found ONE Unix-biased test (no production bug) → fix-forward 4 `58e426c` → merge `cd5d569`, CI green 7/7. Post-merge CURRENT_STATUS update deferred to the Sprint 6.1 kickoff (this entry).
- 2026-11-17 — Sprint 4.5 merged to `main` under standing owner instruction §7 (PD-022): PR creation 403 (PD-020 token state) → local `--no-ff` merge + push; local gates re-verified pre-merge on Linux (pytest 578 passed/7 skipped, ruff/format/mypy clean); CI on the main push watched post-merge.
- 2026-11-16 — Sprint 4.5 (D-028, PD-021): bubblewrap parser sandbox on Linux; containment harness runs contained on Linux (pytest 578 passed/7 skipped, ruff/format/mypy clean); docs + AppArmor prerequisite documented; Windows invariant 575/6 preserved. Branch `claude/linux-parser-sandbox`, not pushed.
- 2026-11-16 — Pending docs landed on `main` by owner instruction: PR #20 merged (`b442033`), PD-019 commits pushed direct (PD-020); merged branches deleted. Linux audit: service pytest 561 passed/20 skipped on Linux; `cargo check` clean on Linux.
- 2026-11-05 — PR #19 merged by owner instruction (PD-018), accepting `DATABASE.md` §9 and PD-016 implementation choices.
- 2026-11-05 — Sprint 4 S4-01..S4-06 implemented/verified on one branch; graph refreshed; PR #19 opened.
- 2026-11-05 — PR #18 merged by owner instruction (PD-017); S-1/S-2 resolved (PD-016, D-027).
- 2026-11-03 — Sprint 3 S3-01..S3-06 implemented/verified on one branch; graph refreshed; PR #18 opened.
- 2026-11-03 — PD-014 (housekeeping, R-04 passed) and PD-015 (Sprint 3 defaults) recorded.
- 2026-11-02 — Owner decisions PD-009..PD-013 recorded; PD-011 safeguards added; PR #17 merged by owner instruction.
- 2026-11-02 — Sprint 2 S2-01..S2-05 implemented/verified on one branch; PR opened for review.
- 2026-11-02 — Sprint 2 prep: merged PR #14 and #15; verified local tests/lint/build (R-00 done).
- 2026-11-02 — Graphify code-graph guidance (PR #15), `--code-only` local use.
- 2026-11-02 — Continuum planning docs created (PR #14).
