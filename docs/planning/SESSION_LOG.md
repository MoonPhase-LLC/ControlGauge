# ControlGauge — Session Log (owner-mandated progress journal)

Append-only, newest first, one entry per pipeline event. Maintained by the scribe subagent.
`CURRENT_STATUS.md` remains the single state tracker; this file is narrative history.

## 2026-11-17 19:10 — Sprint 5 re-review — reviewer

- Sprint 5 re-review verdict: APPROVE (final). Fix commit 5934465 verified: F1 resolved (GET /models serves structural last-known status via _structural_runtime_status → peek_status, no I/O under the manager lock; live checks only on POST /models/{id}/health + start route; response schema unchanged so Rust/TS shapes untouched, Rust delta doc-comment-only; pinning tests honest — status patched to raise → list still 200 with zero health_check_calls delta; models.test.tsx pins "Last known state:" semantics); F2 resolved (DISALLOWED_NAME_CHARS imported from evidence/filetype.py, local copy deleted); N1 resolved (stat+S_ISREG before any open, FIFO test win32-skipped); N2 resolved (closed-client RuntimeError → transport_error, unit tests both roles). Gates: mypy clean linux AND win32 (104 files each); service pytest 725 passed/7 skipped env-live; ruff/format clean; Vitest 70/70 (badge test pins last-known-state semantics at models.test.tsx:184); Playwright 3/3; lint/typecheck clean; cargo test set already proven byte-identical to main (doc-comment-only Rust delta).
- Evidence: 5934465; verdict re-review APPROVE.
- Blockers/open questions: manual UI acceptance run remains operator-gated (open item, not a merge blocker).
- Next: coordinator planning-doc finalization (CURRENT_STATUS + PD-025 + BATCH_JOBS statuses) → §7 merge loop.

## 2026-11-17 19:10 — Sprint 5 fix round — developer

- Sprint 5 fix round complete (fresh developer): commit 5934465 `fix(service): review F1/F2 - structural model status, display-name charset (plus N1/N2 hardening)` — 10 files, +364/−33. F1: GET /models now structural via new ModelRuntimeManager.peek_status (lock held only for dict reads, no I/O), response schema unchanged, Rust/TS comment/copy-only, pinning tests added (test_models_list_is_structural_and_never_checks_the_runtime, test_health_route_still_performs_a_real_check, test_peek_status_is_structural_last_known). F2: routes/models.py imports DISALLOWED_NAME_CHARS from evidence/filetype.py (single source of truth), space-named import test + U+2028 refusal test + per-probe parity test vs clean_filename. N1: stat+S_ISREG before any open → model_file_not_regular reachable, FIFO test (win32 skipif). N2: httpx 0.28 closed-client RuntimeError caught in _request_json → transport_error, unit tests both roles.
- Evidence: gates all green — pytest 725 passed/7 skipped live (+19 new tests), ruff/format clean, mypy strict clean linux AND win32, cargo fmt/clippy clean, cargo test 106p/8f with failing set byte-identical to clean-worktree HEAD d97cd58 (fresh worktree + symlinked venv), Vitest 70/70, Playwright 3/3, lint/typecheck/build clean.
- Blockers/open questions: none
- Next: re-review.

## 2026-11-17 17:51 — Sprint 5 review — reviewer

- Sprint 5 review verdict: APPROVE conditional on two blocking fixes on-branch. Gates re-run from scratch at d97cd58: service 706 passed/7 skipped live (119.8 s); integration module with env unset → 20 skipped in 0.01 s zero errors (Windows CI dependency holds); ruff/format clean; mypy strict clean linux AND win32; cargo fmt/clippy clean; cargo test 106p/8f Linux — proven PRE-EXISTING via temp worktree on main (byte-identical failure set; +1 unrelated flake matching the known-flaky note); zero test names removed/weakened anywhere; Vitest 69/69; Playwright 3/3; lint/typecheck/build clean.
- F1 (blocking): list_models rides the 10s HTTP_REQUEST_TIMEOUT while GET /models performs live capability health checks (60s generation budget) — hung child trips the D-026 fail-closed deadline with no redial. Fix direction: dedicated budget or structural-status list with live checks reserved for POST /models/{id}/health + constant-assertion test.
- F2 (blocking): _DISALLOWED_NAME_CHARS (routes/models.py) adds space+NBSP beyond evidence/filetype.py's class — space-named GGUF files refused 422 with no UI workaround. Fix: mirror filetype class exactly + import test for a space-named file.
- Non-blocking: N1 dead S_ISREG check (FIFO at path = authenticated self-DoS; spec listed the check — fix-forward candidate); N2 httpx closed-client RuntimeError escapes error mapping → should map transport_error; N3 validation-order drift vs spec (harmless); N4 planning-doc finalization belongs to the merge commit (coordinator's); N5 manager.status() lock holds during live checks (Sprint 6 note).
- Rulings: (a) pre-existing Linux cargo failures NOT a blocker (CI runs cargo test windows-only; Windows invariant structurally intact); (b) unshare hard-offline limitation = machine policy, offline guard is sound evidence of record; (c) manual UI acceptance run = open item, not a review blocker, still required before "shipped"; (d) ALL PD-023 owner requirements verified satisfied.
- Blockers/open questions: F1/F2 must land before the §7 merge.
- Next: fresh developer fix round (F1+F2 required, N1+N2 in scope as small same-file hardening), then reviewer re-verification, then coordinator planning-doc finalization + merge.

## 2026-11-17 17:21 — Sprint 5 dev run C — developer

- Developer run C complete — S5-07 d97cd58: exit-flow integration test test_integration_sprint5_exit_flow_offline (own fresh app/manager/database fixture: import both models via D-009 route → start → health ready → nested-schema generation round-trip → embed finiteness/768/determinism → stop both → inventory reports stopped; PASS under the module-wide offline guard) + performance baselines into DEV_MODELS.md §8.
- Evidence: suite 706 passed/7 skipped (integration live: 20 passed in ~53 s); ruff/format/mypy strict clean linux AND win32 (104 files); baselines: b12480 on Threadripper PRO 7985WX CPU-only — generation VmRSS 7928.7 MiB, warm runs ~1273-1293 ms wall, 63 prompt/58 output tokens, ~46.5 tok/s server-side; embedding VmRSS 158.2 MiB, 3.9 ms mean (n=10); embeddings have no server timings → client wall ms (adapted per coordinator); warm prompt cache noted. Unshare hard-offline evidence NOT obtainable (AppArmor apparmor_restrict_unprivileged_userns=1) — recorded in §5.1; offline guard is the evidence of record, not weakened.
- Housekeeping: fixed S5-06 regression — frontend test fixtures used dev-model filenames in app/src/ which tripped test_model_agnostic_source_guard (PD-023); renamed to neutral strings (test files + #[cfg(test)] only). Deviations reported: baselines adapted to live endpoint surface (chat endpoint DOES expose timings; embeddings do not); exit-flow test uses its own fresh fixture for a genuine first-pass demonstration.
- Blockers/open questions: manual UI acceptance run remains operator-gated.
- Next: reviewer pass over the full sprint diff (origin/main..HEAD), then planning-doc updates and the §7 merge loop.

## 2026-11-17 16:37 — Sprint 5 dev run B — developer

- Developer run B complete: S5-05 f163967 (migration 0005_model_files + ModelFile ORM + gguf.py magic/version/size sniff with streamed sha256 + routes/models.py import/list/start/stop/health/get behind D-009 + DATABASE.md §10; 16 files +1896/−12) and S5-06 5959a4c (6 Tauri commands in supervisor/service_api.rs closed-schema pattern + tauri-plugin-dialog with exactly dialog:allow-open + types.ts/commands.ts + ModelManagementScreen with T-08 amber provenance banner + nav/routing + 8 Vitest + Playwright #3; 17 files +1802/−319).
- Evidence: service pytest 686 passed/26 skipped offline (7 platform + 19 integration skips), ruff/format clean, mypy strict clean linux AND win32; real-model integration 19/19 (import contract, truncated file → 502 start_failed with role left empty, dimension learn-and-pin 768 pinned to record); cargo fmt/clippy clean, cargo test 106 passed/8 failed — the 8 proven pre-existing on Linux via stash (Windows-only taskkill tests + one stale pre-bwrap expectation; CI runs cargo test only on windows-latest); Vitest 69/69 (8 new), Playwright 3/3, eslint/tsc/build clean.
- Key fixes self-caught: (1) PDEATHSIG fired on spawning-THREAD death — route-driven starts from FastAPI threadpool workers SIGKILL'd fresh model children; POSIX spawns now fork on a persistent launcher thread, die-with-parent preserved (Linux-only regression tests via collect_ignore_glob, Windows counts unchanged); (2) check_model_health moved off the 10s HTTP_REQUEST_TIMEOUT onto a dedicated 120s budget (service generation health budget is 60s); (3) MODEL_IMPORT_TIMEOUT 300s added for multi-GB hashing; (4) errors.rs cfg_attr(not(windows), allow(dead_code)) on JobObject variants to make the Linux clippy gate runnable (pre-existing failure, precedent 0a31f2a).
- Blockers/open questions: manual dev-box UI acceptance run (import → start → health → stop of the real Qwen model from the built app) needs an operator go — automated evidence (Vitest/Rust/service integration) covers the flow logic; reviewer to rule on the 8 pre-existing Linux cargo-test failures vs §7's local-gates-green rule.
- Next: developer run C implements S5-07 (exit-flow integration test + performance baselines into DEV_MODELS.md), then the reviewer pass.

## 2026-11-17 15:06 — Sprint 4.5 CI watch — coordinator

- CI on be20ded (Windows mypy fix-forward): ALL 7 CHECKS GREEN in ~3 min, including the previously failing "Service (Python 3.12, windows-latest)". Main is green/merged/documented → Sprint 5 push hold lifted; sprint branch pushed to origin.
- Evidence: check-runs for be20ded all success (Windows service, packaged service, Rust supervisor, frontend, Playwright, ubuntu 3.11/3.12).
- Blockers/open questions: none
- Next: developer run A implements S5-01..S5-04 on claude/sprint-5-model-runtime.

## 2026-11-17 15:06 — Sprint 5 dev run A — developer

- Developer run A complete: S5-01..S5-04 implemented on claude/sprint-5-model-runtime, one conventional commit per batch — S5-01 58a2fa1 (config contract + DEV_MODELS.md + MODEL_RUNTIME/SECURITY notes), S5-02 ceea877 (protocols, LlamaCppProvider lifecycle, D-017 credential via LLAMA_API_KEY child env, manager), S5-03 d62fff1 (schema-constrained generation + deterministic validation), S5-04 d7c0ba8 (embeddings + dimension learn-and-pin).
- Evidence: service suite 666 passed/7 skipped (baseline 578/7, +88 tests; 16 real-model integration tests run live); ruff/format clean; mypy strict clean on linux AND --platform win32. Real-model evidence: auth matrix (/health auth-exempt; /props + inference endpoints 401 anonymous), per-launch credential isolation proven (cross-child 401), split-GGUF loads from shard 1, nomic 768-dim finite deterministic embeddings, SIGSTOP hung child detected unhealthy, offline guard green, no flag drift vs b12480 table.
- Key finding recorded in DEV_MODELS.md §5.1 for Sprint 6/8: b12480 json_schema grammar enforces SHAPE not numeric ranges (free-invented 1e1000000000000000 parses to inf; deterministic validator correctly rejects; ~1 in 5 free-form runs) — sampler = shape, validator = ranges; ranged schemas need a retry budget.
- Deviations (reported, spec-consistent): validation.py module created; get_model_runtime dep in routes/deps.py; SECURITY.md evidence in Status column; ENUM_SHAPE_SCHEMA 4th constant + N=5 shape-only claim; types-jsonschema dev dep for strict mypy; auth-matrix test redesigned for per-launch credentials (cross-credential rejection = stronger); _extract_runtime_version handles b12480 flat build_info string. Self-caught bug fixed pre-review: childproc_windows.py ResumeThread ran after the finally closed the thread handle (child would stay suspended) — now resumed inside try after Job assignment.
- Blockers/open questions: none
- Next: developer run B implements S5-05 (GGUF import API + migration 0005) and S5-06 (Rust commands + model management UI).

## 2026-11-17 13:51 — Sprint 4.5 CI fix-forward — coordinator

- CI on the Sprint 4.5 merge (75c6f90): 6/7 checks green, "Service (Python 3.12, windows-latest)" FAILED at the mypy step — exactly the Windows invariant CI exists to catch. Root cause: sandbox_linux.py is never imported on Windows (guarded ImportError) but mypy type-checks it there; under platform=win32 typeshed's POSIX-only `resource` stub resolves empty → "Name 'resource.struct_rusage' is not defined" at line 294. Fixed forward per §7 on claude/ci-win-mypy-sandbox-linux (commit 0a31f2a): platform-conditional RusageType alias (full precision on Linux, Any under win32 type-checking); mypy verified clean on BOTH linux and --platform win32 locally, ruff/format clean, pytest 578/7 unchanged. Merged --no-ff to main (be20ded), branch deleted local+origin; new CI watch running on be20ded. Sprint 5 branch rebased onto the fixed main (kickoff docs now b4451e8).
- Evidence: CI run 44152499949 (Windows mypy failure); fix commit 0a31f2a; merge be20ded; local re-verification mypy linux + win32 clean, pytest 578 passed/7 skipped, containment 19 passed/6 skipped.
- Blockers/open questions: none — sprint branch push held until be20ded CI is green.
- Next: developer run A implements S5-01..S5-04 on claude/sprint-5-model-runtime.

## 2026-11-17 13:40 — Sprint 5 gate — owner

- Sprint 5 gate resolved: owner approved both recommended dev models — Qwen2.5-7B-Instruct Q4_K_M (generation, official Qwen/Qwen2.5-7B-Instruct-GGUF, 2 HF shards) and nomic-embed-text-v1.5 Q4_K_M (embedding, official nomic-ai/nomic-embed-text-v1.5-GGUF) — with 8 additional requirements (model agnosticism, CPU/GPU flexibility, unit/integration test separation with clean skips, schema-validation evidence distinguishing sampler enforcement from semantic quality, embedding tested now but swappable, reproducibility docs, production separation deferred to Sprint 6, performance baselines).
- Evidence: recorded as PD-023 in DECISION_LOG.md; coordinator recorded mechanism default PD-024 (llama-server subprocess + HTTP, D-017 credential via LLAMA_API_KEY env var) — agent-proposed, owner may veto in PR review.
- Blockers/open questions: none
- Next: architect spec.

## 2026-11-17 13:40 — Sprint 5 kickoff — coordinator

- Sprint 5 kickoff + architect stage complete: batch breakdown S5-01..S5-07 written into BATCH_JOBS.md; kickoff docs committed a7457d5 on claude/sprint-5-model-runtime (push held pending main CI); architect produced a 448-line implementation spec (per-batch files/APIs/tests/acceptance + verified llama-server b12480 flag table + auth-matrix method), saved for the developers; dev assets provisioned OUTSIDE the repo at ~/ai-workbench/models/controlgauge/ — both Qwen shards, nomic-embed Q4_K_M, llama.cpp b12480 ubuntu-x64 tarball — all 4 SHA-256s verified OK against official manifests; llama-server runs (version 1.0.0-dev build 12480 commit 7f106b545).
- Evidence: a7457d5; spec at /tmp/opencode/sprint5-spec.md; sha256sum -c → 4× OK.
- Blockers/open questions: none (main-push CI on 75c6f90 still being watched; developer starts only when green).
- Next: developer run A implements S5-01..S5-04 (service model_runtime core) with per-batch conventional commits.

## 2026-11-17 12:53 — Sprint 4.5 merge — coordinator

- Sprint 4.5 merged to `main` under §7: docs/status updates committed on the sprint branch (`b6196e4`), local `--no-ff` merge → merge commit `75c6f90`, pushed to `origin/main`; branch `claude/linux-parser-sandbox` deleted (local + origin), `git fetch --prune` done.
- Evidence: local gates re-verified pre-merge 2026-11-17 — service pytest 578 passed/7 skipped, ruff check / ruff format --check / mypy (77 files) clean; merge diff 14 files, +854/−67; SESSION_LOG.md + PD-022 recorded in DECISION_LOG.md; CURRENT_STATUS baseline updated to merge of bce647f.
- CI watch started on the main push (75c6f90): poll check-runs to completion; fix-forward outranks next sprint if red.
- Blockers/open questions: none
- Next: Sprint 5 owner gate — owner approves the development model (initial GGUF model(s) for dev/testing); then batch breakdown + architect spec on `claude/sprint-5-model-runtime`.

## 2026-11-17 12:50 — V0.1 mission bootstrap — coordinator

- Mission start: owner-directed implementation of Sprints 5–16 via specialist pipeline; bootstrap executed in order (CLAUDE.md, AGENT_INSTRUCTIONS.md, CURRENT_STATUS, MASTER_PLAN, BATCH_JOBS, EXECUTION_RULES, SPRINTS.md Sprint 5, MODEL_RUNTIME.md).
- Git state verified: on `claude/linux-parser-sandbox` @ bce647f, clean tree, in sync with origin; `origin/main` @ 3b29aa6 — Sprint 4.5 (reviewer-APPROVED) NOT yet merged; branch descends cleanly from main (5 commits ahead).
- CI triggers verified (ci.yml): pull_request + push to main only → 0 check-runs exist for the Sprint 4.5 branch head; Windows verification will ride the main push.
- Blockers/open questions: none
- Next: merge Sprint 4.5 under standing owner instruction §7.

## 2026-11-17 12:50 — Sprint 4.5 merge — coordinator

- Decision: merge `claude/linux-parser-sandbox` (Sprint 4.5, D-028/PD-021, reviewer APPROVE after F1/F2 fixes) into `main` under §7 of the mission prompt ("ship every sprint yourself — owner-authorized merge loop"), rather than pausing to ask: §7 explicitly overrides merge-approval pauses and Sprint 4.5 meets every §7 shippability condition. Bootstrap step 3's "pause and ask" is superseded by §7's explicit no-pause instruction in the same owner document.
- PR-creation probe: POST /pulls → 403 "Resource not accessible by personal access token" — confirms PD-020 token state. Fallback per §7: merge locally into main and push; watch push-to-main CI to completion; fix-forward outranks next sprint if red.
- Evidence: local gates re-run on Linux 2026-11-17 before merge (results recorded in the next entry by the coordinator).
- Blockers/open questions: none
- Next: docs/status updates committed on the sprint branch, then local --no-ff merge, push, CI watch.

## 2026-11-17 12:50 — journal bootstrap — scribe

- Scribe journal initialized per owner requirement §3a of the mission prompt; will record every pipeline stage (architect spec, developer commits, review verdicts, gate runs, owner decisions, merges, blockers) append-only, newest first.
- Blockers/open questions: none
- Next: journal Sprint 4.5 merge completion and Sprint 5 kickoff stages as the coordinator reports them.
