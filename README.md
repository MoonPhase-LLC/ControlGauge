# ControlGauge

**Private AI. Measurable Compliance.**

ControlGauge is a local-first cybersecurity compliance application for analysts. It is being
built to help them understand how their evidence (policies, procedures, technical artifacts,
configurations, reports, spreadsheets, logs) relates to cybersecurity framework controls, using
AI-assisted analysis that stays on the analyst's machine. That AI analysis is planned, not built
yet; see Project Status. The first framework is **NIST SP 800-53 Revision 5**. Other frameworks
are out of scope for V0.1, but the architecture must not rule them out (`docs/PRODUCT.md` §6).

ControlGauge was previously named EvidenceGraph. See [`docs/PACKAGING.md`](docs/PACKAGING.md) →
"Migrating from EvidenceGraph" for existing test installs and developer environments.

## Design Principles

These principles govern the design. The evidence-processing and AI features they describe are
planned, not implemented yet (see Project Status).

- **Local-first.** Customer evidence is to be processed, embedded, retrieved against, and
  evaluated by a locally-run LLM. Nothing evidence-bearing is sent to a cloud AI provider. There
  is no automatic cloud fallback if local inference is unavailable: a failure is surfaced, never
  silently routed around.
- **Privacy.** Organizational data stays on the analyst's machine. See `docs/SECURITY.md` for
  the full threat model.
- **Evidence traceability.** Every AI suggestion is to carry full provenance (artifact, section,
  control, model, confidence, reasoning, review status).
- **Human oversight.** The AI is to suggest candidate relationships between evidence and
  controls, with confidence scores and reasoning. It never independently determines compliance.
  Every suggestion requires human analyst review.
- **Auditability and extensibility.** A clear audit trail, a modular architecture, and
  framework logic that is not hardcoded around a single standard.
- **Untrusted input by default.** Uploaded documents, imported models, and LLM output are
  treated as untrusted data at every layer — never as instructions.

See `docs/PRODUCT.md` for the complete set of product principles.

## Project Status

**Sprint 1 — Desktop Foundation (in progress).** Sprint 0 produced the product,
architecture, security, and planning documentation. Completed on `main` so far:

- **S1-02:** a Tauri v2 desktop shell under [`app/`](app/), with a React + TypeScript
  frontend (Tailwind CSS v4, shadcn/ui).
- **S1-03:** a session-token-authenticated Python/FastAPI local service under
  [`service/`](service/), with one `GET /health` endpoint.
- **S1-04:** the desktop app launches and supervises that service. It verifies the service's
  identity and makes the authenticated health check in Rust; the frontend never sees the
  credential.
- **S1-05:** the frontend has a basic desktop layout. It has navigation placeholders that are not
  usable yet, and it shows the local service's status and health result.
- **S1-06:** test runners. Vitest for the frontend, the existing pytest suite for the service, a
  Playwright browser smoke test, and an opt-in Windows native smoke test (see `app/README.md`).
- **S1-07:** GitHub Actions CI for the frontend, service and Rust supervisor checks.
- **S1-09:** a Windows installer that bundles the service, built by
  `scripts/package-windows.ps1`. It passed a manual test on a clean Windows machine (see
  [`docs/PACKAGING.md`](docs/PACKAGING.md)).

There is still no evidence handling, database, model runtime, or working assessment feature.
Remaining Sprint 1 work: Sprint 1 exit validation (S1-08).
See `docs/SPRINT_1_BACKLOG.md` for ticket details, `docs/SPRINTS.md` for the full sprint plan,
and `docs/OPEN_QUESTIONS.md` for decisions still pending product-owner input.

### Roadmap (planned, not implemented)

| Capability | Status |
|---|---|
| Tauri desktop shell + supervised local service | Implemented (Sprint 1) |
| Authenticated service with challenge-based identity verification | Implemented (Sprint 1) |
| Evidence ingestion and document parsing | Planned (Sprint 2+) |
| Local LLM runtime (llama.cpp + GGUF) and embedding/retrieval pipeline | Planned |
| AI-suggested evidence ↔ control mappings with human review workflow | Planned |
| SQLite persistence for artifacts, controls, findings | Planned |
| NIST SP 800-53 Rev 5 framework support | First framework; loading/evaluation work planned |
| Additional frameworks | Out of scope for V0.1; architecture must not preclude them (`docs/PRODUCT.md` §6) |
| Packaged installer distribution | S1-09 spike, then Sprint 16 |

## Getting Started

Prerequisites (exact minimums and tested versions in [`CONTRIBUTING.md`](CONTRIBUTING.md)):

- **Node.js** 22.12.0+ (24.x recommended)
- **Rust** via [rustup](https://rustup.rs/) — the pinned toolchain (`app/src-tauri/rust-toolchain.toml`, currently 1.98.0) is installed and selected automatically
- **Python** 3.11+ (3.12 recommended) via [`uv`](https://docs.astral.sh/uv/)
- **Windows** development prerequisites for the Tauri stack (MSVC C++ Build Tools, WebView2) — see `CONTRIBUTING.md`

### Run the desktop app (development)

```sh
# from app/
npm install

# from service/  (required: dev-mode supervision launches service/.venv's Python)
uv sync

# back in app/
npm run tauri dev        # launches the Tauri desktop window; the app spawns and supervises the service
```

`npm run dev` (app/) starts the Vite dev server only — the frontend in a browser, no Tauri
window.

### Run the service standalone

```sh
# from service/
uv sync

# a session token is required before any request succeeds (including /health):
CONTROLGAUGE_SESSION_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))") \
  uv run controlgauge-service
```

The service listens on `127.0.0.1:51823` by default. Full configuration (`CONTROLGAUGE_*`
environment variables) and credential-format rationale: [`service/README.md`](service/README.md).

### Run the tests

```sh
# Frontend — from app/ (after npm ci)
npm run lint
npm run build             # strict typecheck + production Vite build
npm run typecheck:test    # strict typecheck of test code and configs
npm test                  # Vitest unit/component tests
npm run test:e2e          # Playwright browser smoke (needs: npx playwright install chromium)

# Service — from service/ (after uv sync)
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy

# Rust supervisor — from app/src-tauri/
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test                # unit + real-process supervisor tests (needs: uv sync in service/)

# Opt-in native smoke test (Windows only, not in CI): see app/README.md
npm run test:e2e:native
```

CI (`.github/workflows/ci.yml`) runs the frontend, browser-smoke, service, and Rust jobs on
every PR to `main`; the native smoke test is deliberately run locally only — see
`CONTRIBUTING.md` for why.

### Build the app

```sh
# from app/
npm run tauri build       # release build only, without the bundled service
```

A release build needs the frozen service bundled next to `app.exe`, and fails closed without it.
To build the Windows installer with the service included, run `scripts/package-windows.ps1`
(see [`docs/PACKAGING.md`](docs/PACKAGING.md)).

## Repository Structure

```
.
├── app/                  # Tauri v2 desktop shell
│   ├── src/              # React + TypeScript frontend
│   ├── src-tauri/        # Rust: Tauri config + supervisor (spawns/authenticates the service)
│   ├── e2e/              # Playwright browser smoke test
│   └── e2e-native/       # Opt-in Windows native smoke test
├── service/              # Python/FastAPI local analysis service (loopback-only)
│   ├── src/controlgauge_service/
│   └── tests/
├── frameworks/           # Framework data (NIST SP 800-53 Rev 5; more to come)
├── docs/                 # Product, architecture, security, and sprint documentation
├── .github/workflows/    # CI (see above)
├── CLAUDE.md             # Agent workflow rules for this repository
└── CONTRIBUTING.md       # Toolchain versions, conventions, working commands
```

## Developer Documentation

Start here, in order:

1. [`docs/PRODUCT.md`](docs/PRODUCT.md) — problem, users, value proposition, V0.1 scope and
   non-goals.
2. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — components, trust boundaries, stack
   evaluation.
3. [`docs/SECURITY.md`](docs/SECURITY.md) — threat model, mitigations, unresolved risks.
4. [`docs/COMPLIANCE_MODEL.md`](docs/COMPLIANCE_MODEL.md) — the entity/relationship model
   behind the compliance knowledge graph.
5. [`docs/AI_PIPELINE.md`](docs/AI_PIPELINE.md) — parsing, retrieval, LLM evaluation,
   structured output, validation.
6. [`docs/MODEL_RUNTIME.md`](docs/MODEL_RUNTIME.md) — `ModelProvider` abstraction and local
   model lifecycle.
7. [`docs/DATABASE.md`](docs/DATABASE.md) — proposed schema and ER diagram.
8. [`docs/USER_FLOWS.md`](docs/USER_FLOWS.md) — intended user flows and open UX questions.
9. [`docs/SPRINTS.md`](docs/SPRINTS.md) — full sprint plan, Sprint 0–16.
10. [`docs/SPRINT_1_BACKLOG.md`](docs/SPRINT_1_BACKLOG.md) — detailed Sprint 1 tickets.
11. [`docs/DECISIONS.md`](docs/DECISIONS.md) — architecture decision log (ADRs).
12. [`docs/OPEN_QUESTIONS.md`](docs/OPEN_QUESTIONS.md) — questions requiring product-owner
    decision, categorized.
13. [`docs/AGENT_INSTRUCTIONS.md`](docs/AGENT_INSTRUCTIONS.md) — binding rules for any
    coding agent working in this repository.

Any coding agent (human or AI) should read `docs/AGENT_INSTRUCTIONS.md` and the relevant
subsystem doc before making changes. See also the root [`CLAUDE.md`](CLAUDE.md) for the git
workflow and forbidden actions, and [`CONTRIBUTING.md`](CONTRIBUTING.md) for repository
conventions and tooling.

## Technology

Tauri (desktop shell) · React + TypeScript + Tailwind/shadcn-ui (frontend) · Python + FastAPI
(local analysis service) · SQLite + SQLAlchemy (storage, planned) · llama.cpp + GGUF (local
inference, planned). Full evaluation in `docs/ARCHITECTURE.md`.

## License

TBD.
