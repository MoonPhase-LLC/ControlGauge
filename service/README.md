# ControlGauge Local Service

Standalone Python/FastAPI local analysis service skeleton. Bound to exactly `127.0.0.1` -- the
documented S1-03 bind contract (`docs/SPRINT_1_BACKLOG.md` S1-03, `docs/ARCHITECTURE.md` §4,
`docs/SECURITY.md` T-09) -- and no other address or loopback form (`::1`, other `127.x.x.x`
addresses, `localhost`, `0.0.0.0`, etc.); every request, including `GET /health`, requires the
installed session credential. See the root [`CONTRIBUTING.md`](../CONTRIBUTING.md) for toolchain
versions and [`docs/SPRINT_1_BACKLOG.md`](../docs/SPRINT_1_BACKLOG.md) S1-03 for this ticket's
scope, and [`docs/DECISIONS.md`](../docs/DECISIONS.md) D-009/D-018/D-025 for the full
authentication/startup architecture this is one piece of.

**Standalone only, for now.** This process is not yet launched or supervised by the Tauri desktop
shell (`app/`) — that is S1-04. Run it by hand for local development/testing, with the session
token supplied via an environment variable, exactly as documented below.

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.11+ (3.12 recommended — see root
`CONTRIBUTING.md`). From this directory:

```sh
uv sync
```

This creates `.venv/` and installs exact versions pinned in `uv.lock`.

## Running

A session token is required before any request succeeds — including `/health` (see
`docs/DECISIONS.md` D-009: there is no unauthenticated health exemption).

**Accepted credential format:** an ASCII string of at least 16 characters, using only the
unpadded Base64URL alphabet (`A-Z`, `a-z`, `0-9`, `-`, `_`; RFC 4648 §5, no `=` padding). This is
exactly the alphabet `secrets.token_urlsafe()` produces, so every generated credential round-trips
cleanly through an `Authorization: Bearer <token>` header (RFC 9110 §11.3's `token68` grammar). A
value outside this format is treated the same as no credential configured — every request fails
closed with `401`, and the invalid value is never logged. Generate a temporary development-only
credential without committing or printing a production secret:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```sh
CONTROLGAUGE_SESSION_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))") uv run controlgauge-service
```

The service prints a structured JSON startup log line to stderr and then listens on
`127.0.0.1:51823` by default. Configuration is entirely environment-variable driven (all
`CONTROLGAUGE_*`, see `src/controlgauge_service/config.py`):

| Variable | Default | Notes |
|---|---|---|
| `CONTROLGAUGE_HOST` | `127.0.0.1` | Must be exactly `127.0.0.1` — the documented S1-03 bind contract; any other value (`::1`, another `127.x.x.x` address, `localhost`, `0.0.0.0`, a LAN/public address, ...) refuses to start, before any listener opens. |
| `CONTROLGAUGE_PORT` | `51823` | 1-65535. |
| `CONTROLGAUGE_ENVIRONMENT` | `production` | `development` additionally exposes `/docs`, `/redoc`, `/openapi.json` (still authenticated). |
| `CONTROLGAUGE_SESSION_TOKEN` | *(unset)* | Standalone/test credential only, in the format documented above. In supervised (Tauri-launched) mode, S1-04 installs this over a private channel instead — never via this variable. Missing, too short, or wrong-format means every request is rejected (fail closed), not that auth is skipped. |

Exercise it once running (`Bearer` is matched case-insensitively, per RFC 9110 §11.1, and every
`401` response includes a `WWW-Authenticate: Bearer` header):

```sh
curl -i http://127.0.0.1:51823/health   # -> 401, no token supplied
curl -H "Authorization: Bearer <your-generated-token>" http://127.0.0.1:51823/health
```

## Local database (Sprint 2)

The service stores assessments in a local SQLite file, `controlgauge.db`, in the OS per-user,
machine-local app-data directory (owner decision PD-006 / `docs/OPEN_QUESTIONS.md` A-2):

| OS | Directory |
|---|---|
| Windows | `%LOCALAPPDATA%\ControlGauge` (Local, never Roaming) |
| macOS | `~/Library/Application Support/ControlGauge` |
| Linux | `$XDG_DATA_HOME/controlgauge` (default `~/.local/share/controlgauge`) |

A user-configurable location is deferred. `CONTROLGAUGE_DATA_DIR` (absolute path) overrides the
directory for **development and tests only** (PD-008); the test suites set it so they never touch
the real database. Uninstalling the app does not delete this directory.

At startup, before any socket is opened, the service creates the directory if needed, opens the
database (`PRAGMA foreign_keys = ON` on every connection) and applies all pending Alembic
migrations. If any of that fails, the process exits non-zero with a fixed reason
(`data_dir_create_failed`, `database_migration_failed`, ...) and never serves; a database from a
newer app version (unknown revision) also fails closed. The path is never logged; the log records
only `database_ready` with the schema revision.

Migrations live in `src/controlgauge_service/migrations/` (packaged as data by
`scripts/package-windows.ps1`). To author a new one against a scratch database:

```sh
uv run alembic revision --autogenerate -m "describe the change"   # uses alembic.ini
```

## Endpoints

Every endpoint requires `Authorization: Bearer <session token>`; missing or wrong credentials get
`401 {"detail": "Unauthorized"}` before routing (no exemptions besides the D-025 startup
challenge in supervised mode).

| Method | Path | Success | Notes |
|---|---|---|---|
| `GET` | `/health` | 200 | S1-03 |
| `GET` | `/frameworks` | 200 `{"frameworks": [{id, name, version, source_reference, control_count}]}` | Metadata only; `control_count` is 0 until Sprint 3 loads a catalog |
| `GET` | `/assessments?limit=&offset=` | 200 `{"assessments": [...], total, limit, offset}` | Newest first; `limit` 1–100 (default 50) |
| `POST` | `/assessments` | 201 assessment | Body `{"name", "framework_id", "description"?}`; unknown fields rejected |
| `GET` | `/assessments/{id}` | 200 assessment | Unknown or non-canonical UUID → 404 `assessment_not_found` |

An assessment is `{id, name, description, framework: {id, name, version}, created_at}`
(`created_at` is ISO 8601 UTC). Validation: `name` trimmed, 1–200 characters, no control
characters or line breaks; `description` optional, trimmed (blank → `null`), ≤ 2000 characters,
line breaks and tabs allowed; `framework_id` must name a registered framework.

Errors on these routes share one shape and never echo submitted values:

```json
{"error": {"code": "validation_failed", "message": "The request is not valid.",
           "fields": [{"field": "name", "code": "too_long", "message": "This value is too long."}]}}
```

Top-level codes: `validation_failed` (422), `malformed_body` (400), `assessment_not_found` (404),
`database_unavailable` (503). Field codes: `required`, `blank`, `too_long`, `invalid_characters`,
`invalid_type`, `unknown_field` (reported against `body`), `framework_not_found`, `invalid`.
Unexpected failures return the existing generic `500 {"detail": "Internal Server Error"}`.

## Entry points

Both of the following start the identical service with identical configuration behavior — the
module entry point is a thin shim over the same `main()` the console script uses:

```sh
uv run controlgauge-service
uv run python -m controlgauge_service
```

## Tests, lint, and types

```sh
uv sync --frozen   # install exactly the locked dependencies, including the dev group
uv run pytest      # the full service test suite (tests/), exits non-zero on any failure
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

`pytest` is configured in `pyproject.toml` (`[tool.pytest.ini_options]`).
- `tests/test_health.py` and `tests/test_auth.py` cover the S1-03 `/health` endpoint and session-token authentication in process.
- `test_server_process.py`, `test_supervised_process.py` and `test_supervised_app.py` start the real service as a child process, bound to `127.0.0.1`.
- These tests exercise the Python service on its own. Tauri's supervision of the service is tested on the Rust side (`cargo test` in `app/src-tauri/`) and by the native smoke test in `app/` (see `app/README.md`).
