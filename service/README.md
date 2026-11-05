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
directory for **development and tests only** (PD-008/PD-011); the test suites set it so they never
touch the real database. It must be a local path: UNC/device paths (`\\server\share`, `\\?\...`,
`//server/share`) and Windows mapped network drives are refused with
`data_dir_override_remote`, and startup fails closed (SQLite on network storage risks corruption).
Network detection for other POSIX mounts is not implemented. **Production behavior:** the
packaged service also honors the variable if it is set in the user's environment (CI's frozen-exe
tests rely on this); it is not exposed in the UI or documented to end users. Uninstalling the app
does not delete the data directory.

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

## Framework catalogs (Sprint 3)

Each `frameworks/<id>/framework.json` names a framework and may declare its catalog: an OSCAL JSON
catalog file plus optional OSCAL profiles (published control selections), each pinned by
SHA-256, with the release version, upstream tag/commit/URLs, license notice and attribution.
At startup, after migrations and before any socket opens (`catalog_import.py`):

1. A declared release that is already imported is **verified** (stored hash equals the
   manifest's); a different hash under the same version is refused (`release_conflict`) and the
   stored release is left untouched.
2. Otherwise each file's size and SHA-256 are checked **before** parsing, then `oscal.py`
   validates it (sizes, counts, nesting depth, field lengths, OSCAL identifier syntax and
   uniqueness, no control characters, every parameter reference resolves, duplicate JSON keys
   and NaN refused). Shapes it cannot store faithfully -- nested groups, controls nested deeper
   than one enhancement level, profiles that modify, exclude, match by pattern or import several
   sources -- are refused, never simplified. The whole release is written in one transaction.
3. The release becomes the framework's current one, and assessments of that framework with no
   pinned release are pinned to it (once).

A refused source is logged as `catalog_import_refused` with a fixed `reason`; the service still
starts and that framework simply has no catalog. Source text is never logged. The importer is
framework-agnostic: identifiers come from a control's unclassed `label` property (else its OSCAL
`id`) and are never parsed. A first import of the shipped catalog takes about 1.5 s; later
starts only verify (about 20 ms).

## Evidence ingestion (Sprint 4)

Uploads (`routes/artifacts.py`) are checked in this order before anything is stored: the
assessment exists; size ≤ 50 MB (declared `Content-Length` and while streaming); the display name
(`X-ControlGauge-Filename`, percent-encoded UTF-8) is clean; the extension is `.pdf`, `.docx`,
`.txt` or `.csv`; the leading bytes match it; the assessment stays within 1,000 files and 2 GB.
An exact duplicate (same SHA-256 in the same assessment) is **flagged**: 200 with
`duplicate: true` and the existing artifact; nothing is stored or parsed again. Every limit lives
in `evidence/limits.py` (owner decision PD-016).

Files are stored as `<data dir>/evidence/<assessment id>/<sha256>.bin` (`evidence/store.py`); the
client's name is display text only. One background thread (`evidence/ingest.py`) parses each new
artifact **inside the parser sandbox** (`evidence/sandbox_windows.py`, `docs/DECISIONS.md`
D-027): a per-parse less-privileged AppContainer with no network capability, given the file as a
read-only stdin handle, in a Job Object (1 GB memory, 60 s CPU, one process) with a 120 s
wall-clock and 48 MB output cap. The worker (`evidence/worker.py`, `evidence/parsers.py`; pypdf for
PDF, standard library for the rest) returns JSON that the service validates before storing
sections. Each outcome is one `parse_status` (`parsed`, `partial`, `empty`, `unsupported_format`,
`failed`) with a fixed `parse_detail` code (e.g. `encrypted`, `archive_limit`, `timeout`).
Interrupted parses are re-queued at startup.

- **Windows only.** On other platforms parsing fails closed (`failed` /
  `containment_unavailable`). Tests there inject a test-only unconfined runner explicitly.
- **First parse on a machine** grants the `controlGaugeParserRuntime` capability read/execute on
  the worker's program directories (inherited ACE): in development the base Python install,
  `site-packages` and `src/`; in the frozen build the bundle directory. Nothing else changes.
- `tests/test_containment.py` is the D-022 containment harness (a hostile probe must be denied
  evidence, the database, writes outside scratch, the network and child processes, and be stopped
  at each bound). `--service-exe <exe>` also runs the frozen worker contained.

## Endpoints

Every endpoint requires `Authorization: Bearer <session token>`; missing or wrong credentials get
`401 {"detail": "Unauthorized"}` before routing (no exemptions besides the D-025 startup
challenge in supervised mode).

| Method | Path | Success | Notes |
|---|---|---|---|
| `GET` | `/health` | 200 | S1-03 |
| `GET` | `/frameworks` | 200 `{"frameworks": [{id, name, version, source_reference, control_count, current_release}]}` | `current_release` `{id, version, title}` or `null`; `control_count` counts its controls |
| `GET` | `/assessments?limit=&offset=` | 200 `{"assessments": [...], total, limit, offset}` | Newest first; `limit` 1–100 (default 50) |
| `POST` | `/assessments` | 201 assessment | Body `{"name", "framework_id", "description"?}`; unknown fields rejected |
| `GET` | `/assessments/{id}` | 200 assessment | Unknown or non-canonical UUID → 404 `assessment_not_found` |
| `GET` | `/catalog-releases/{id}` | 200 release | Provenance (`source`, `license_notice`, `attribution`), counts, `families` (with any introductory `parts`), `baselines`; 404 `catalog_release_not_found` |
| `GET` | `/catalog-releases/{id}/controls?family_id=&baseline_id=&q=&limit=&offset=` | 200 `{"controls": [...], total, limit, offset}` | Source order; `q` (≤ 100 chars) matches identifier/title of the control or its enhancements, literally (no wildcards); `limit` 1–500 (default 100) |
| `POST` | `/assessments/{id}/artifacts` | 201 `{artifact, duplicate: false}` or 200 `{artifact, duplicate: true}` | Raw file bytes as the body; name in `X-ControlGauge-Filename`; see "Evidence ingestion" |
| `GET` | `/assessments/{id}/artifacts?limit=&offset=` | 200 `{"artifacts": [...], total, total_bytes, limit, offset, limits}` | Newest first; `limit` 1–200 (default 100) |
| `GET` | `/artifacts/{id}` | 200 artifact | `{id, assessment_id, filename, sha256, size_bytes, format, mime_type, parse_status, parse_detail, parser_version, section_count, uploaded_at, parsed_at}`; 404 `artifact_not_found` |
| `GET` | `/artifacts/{id}/sections?limit=&offset=` | 200 `{artifact_id, sections: [{id, sequence_index, locator, content}], total, limit, offset}` | Extracted text in order; `limit` 1–200 (default 50) |
| `GET` | `/controls/{id}` | 200 control | Verbatim `parts` tree, `params`, `links` (each with a resolved `target`: control, enhancement, part, family, resource, external or unresolved), `enhancements`, `baselines`; 404 `control_not_found` |

An assessment is `{id, name, description, framework: {id, name, version}, catalog_release,
created_at}` (`catalog_release` is the pinned `{id, version, title}` or `null`)
(`created_at` is ISO 8601 UTC). Validation: `name` trimmed, 1–200 characters, no control
characters or line breaks; `description` optional, trimmed (blank → `null`), ≤ 2000 characters,
line breaks and tabs allowed; `framework_id` must name a registered framework.

Errors on these routes share one shape and never echo submitted values:

```json
{"error": {"code": "validation_failed", "message": "The request is not valid.",
           "fields": [{"field": "name", "code": "too_long", "message": "This value is too long."}]}}
```

Top-level codes: `validation_failed` (422), `malformed_body` (400), `assessment_not_found`,
`catalog_release_not_found`, `control_not_found`, `artifact_not_found` (404),
`database_unavailable`, `ingestion_unavailable` (503); uploads also `file_too_large` (413),
`unsupported_file_type`, `file_type_mismatch` (415), `invalid_filename` (422),
`assessment_artifact_limit`, `assessment_size_limit` (409). Field codes: `required`, `blank`, `too_long`, `invalid_characters`,
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
