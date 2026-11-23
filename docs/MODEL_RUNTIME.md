# ControlGauge — Local Model Runtime

Status: Sprint 6 contract (§3–§6 rewritten by S6-01; §1/§2/§8–§11 unchanged).

## 1. `ModelProvider` Abstraction

The AI pipeline (`AI_PIPELINE.md`) depends only on capability interfaces, never on a specific
runtime, and never on the assumption that one model instance must serve every capability
(`DECISIONS.md` D-011 — this revises the original single-interface sketch, which bundled
generation and embedding together as if one loaded model always does both):

```
GenerationCapable:
  generate(prompt: str, schema: JSONSchema, timeout_s: float) -> StructuredResult | ProviderError
  health_check() -> HealthStatus                        # up/down, model loaded, version info
  describe() -> ModelDescriptor                          # id, version, context window, capabilities

EmbeddingCapable:
  embed(text: str) -> list[float]                      # for retrieval, §AI_PIPELINE.md §5
  health_check() -> HealthStatus
  describe() -> ModelDescriptor
```

A concrete provider (e.g. `LlamaCppProvider`) may implement one or both interfaces. The pipeline
asks for "the active generation provider" and "the active embedding provider" independently — they
may be the same underlying process/model if a chosen GGUF model is adequate at both, or two
separate `LlamaCppProvider` instances loaded with different models, whichever gives acceptable
retrieval and generation quality (an empirical Sprint 5/6 decision, informed by an early
retrieval-quality check — see `SPRINTS.md` Sprint 8).

V0.1 ships exactly one provider implementation: `LlamaCppProvider`. The interface exists so that
`OllamaProvider`, `VLLMProvider`, or an `EnterprisePrivateProvider` could be added later without
touching pipeline code — not because V0.1 needs more than one.

`ModelProvider` implementations must never make external network calls as part of `generate`/`embed`.
Authenticated loopback IPC is permitted; external calls are confined to the explicit model
download/catalog flow (§3, §6).

## 2. `LlamaCppProvider`

Runs llama.cpp as a local subprocess (server mode), bound to `127.0.0.1` on a locally allocated
port, and communicates with it over that local HTTP interface, or via direct bindings — exact
mechanism (subprocess + HTTP vs. Python bindings) is an implementation decision for Sprint 5, not
fixed here; either is acceptable as long as it stays local-only and fits the `ModelProvider`
interface.

**Mechanism (PD-024, decided for Sprint 5):** the official `llama-server` binary runs as a
**supervised subprocess** bound to `127.0.0.1` on an OS-allocated port, authenticated per D-017
(fresh per-launch credential delivered in the child-scoped environment variable `LLAMA_API_KEY` —
never argv, never logged, never sent to the frontend; see `SECURITY.md` T-23). Structured output
uses llama-server's JSON-schema-constrained sampling via `/v1/chat/completions`
`response_format` — the pipeline's deterministic validation (`AI_PIPELINE.md` §8) remains
mandatory regardless. Dev binary/models: `DEV_MODELS.md`.

Structured output: rely on llama.cpp's grammar/JSON-schema-constrained sampling where available,
to increase the odds that `generate()` returns schema-conformant output. Even so, the pipeline's
deterministic validation (`AI_PIPELINE.md` §8) is mandatory — constrained sampling reduces but
does not eliminate the need to validate.

## 3. Model Catalog (Sprint 6 contract)

### 3.1 Two-tier model universe (PD-026)

The model universe has **two tiers, distinguished end to end**:

- **Tier 1 — curated catalog**: the in-repo, versioned manifest
  `models_catalog/catalog.json` (+ `models_catalog/profiles.json`), revision-pinned to exact
  Hugging Face commits, SHA-256-verified on download, owner-approved per release. A
  **convenience, not a capability ceiling**: profiles and manifest entries may exist above the
  curated sizes, and the user-managed registry accepts arbitrary compatible models (§7).
- **Tier 2 — user-managed registry**: everything the user brings — local GGUF files (§7 import
  path), direct URL imports (§6), and registered external local inference servers (a Sprint 6
  registry capability; loopback-only). Imported files are untrusted until validated; catalog
  entries are **never** written into the registry by browsing — only an explicit user-approved
  download creates a registry entry from the catalog.

The distinction is persisted (`model_file.origin`/`verification`, migration `0006_model_registry`)
and visible in the UI (origin + verification badges). Curated vs user-managed must be clear
without reading code (PD-026).

### 3.2 Manifest schema (normative for the shipped `models_catalog/catalog.json`)

One JSON object, strictly validated on load (the `framework_catalog.py` pattern: pydantic
`extra="forbid"` everywhere, size cap, fail-closed refusal — a manifest that does not validate
makes the catalog **unavailable**, never partially loaded):

```json
{
  "manifest_version": 1,
  "catalog_version": "<semver-ish string, bumped with every content change>",
  "entries": [
    {
      "model_id": "...",
      "display_name": "...",
      "format": "gguf",
      "backend": "llama_cpp",
      "roles": ["generation"],
      "tier": "BALANCED",
      "recommended": true,
      "recommendation_withheld_reason": null,
      "context_window": 32768,
      "curated_context_tokens": 8192,
      "max_input_tokens": 8192,
      "max_output_tokens": 512,
      "tokenizer": "...",
      "footprint": {
        "runtime_overhead_bytes": 0,
        "kv_cache_bytes_per_token": 0,
        "notes": "..."
      },
      "embedding_dimensions": 768,
      "embedding_normalization": "l2",
      "embedding_similarity_metric": "cosine",
      "files": [
        {
          "filename": "...",
          "size_bytes": 0,
          "sha256": "<64 hex>",
          "download_url": "https://huggingface.co/<repo>/resolve/<40-hex-revision>/<filename>"
        }
      ],
      "source": {
        "repository_url": "https://huggingface.co/<repo>",
        "revision": "<40 hex, never a moving ref>",
        "revision_ref": "main",
        "license": "<SPDX id from the model card>",
        "license_url": "..."
      },
      "pairing_default": false,
      "evaluation": {"status": "unevaluated", "suite_version": null, "results": null}
    }
  ]
}
```

- `roles` (`DECISIONS.md` D-023) declares the capabilities an entry supports. Capability-specific
  fields are required-forbidden by role: embedding entries carry `embedding_dimensions`,
  `embedding_normalization` (`"none"`/`"l2"`), `embedding_similarity_metric`
  (`"cosine"`/`"dot_product"`) and set `kv_cache_bytes_per_token` to `0`; `max_output_tokens` is
  forbidden on embedding-only entries. Both roles carry `max_input_tokens` + `tokenizer` identity
  (D-025); embedding inputs are chunked within the model's limit, never silently truncated.
- **Footprint block, not flat requirements.** Requirements are *computed* from footprint +
  detected hardware (§5) — never stored as per-OS flat fields that drift. `kv_cache_bytes_per_token`
  and `runtime_overhead_bytes` are author-assigned estimates; the formula that consumes them is
  documented in §5 and pinned by unit tests.
- **Tier** stays author-assigned (FAST/BALANCED/ACCURATE product grouping; tier is not computed
  from specs — separation of concerns from the Sprint 0 brief).
- **Recommendation gate (no-ceiling rule):** an entry is *shown as* "Recommended" only when
  `recommended: true` **and** feasibility passes (§5). Any future >14B entry ships
  `recommended: false` with `recommendation_withheld_reason: "evaluation_suite_pending"` until the
  Sprint 14 evaluation suite exists — larger entries may be listed *feasible*, never auto-promoted.
- **Reserved evaluation field (hallucination minimization, PD-026 highest priority):** every entry
  carries `evaluation`; the loader refuses any value except `status: "unevaluated"` in V0.1
  (fail-closed against fabricated evaluation data until the suite exists — `AI_PIPELINE.md` §13).
- `files[]` download URLs always embed the **40-hex commit revision**, never a moving ref such as
  `main`: pinned `resolve/<sha>/` URLs are immutable.
- `pairing_default`: exactly one generation and one embedding entry per manifest are the default
  pairing (D-023's combined-footprint evaluation pair).

### 3.3 Recommendation profiles (`models_catalog/profiles.json`)

Synthetic hardware profiles exist so recommendations can be evaluated against the owner's primary
targets without that hardware; they never gate the detected-hardware path:

| profile_id | RAM | VRAM (aggregate) | GPUs |
|---|---|---|---|
| `consumer-cpu` | 16 GiB | 0 | 0 |
| `workstation-32gb` | 64 GiB | 32 GiB | 1 |
| `enterprise-288gb` | 256 GiB | 288 GiB | 8 |

## 4. Hardware Detection (Sprint 6 contract)

Local-only, best-effort per field: an unknown field is `None`/empty, **never an error** —
`detection_complete` is false when any expected field is unknown, and an empty GPU list means
"nothing detected" (which recommendations must treat as CPU-only, honestly — on Linux,
`gpus: []` is a *valid result*, not a failure). Named mechanisms per platform, stdlib + `ctypes`
only (a detection dependency such as `psutil`/`comtypes`/WMI would each be a new dependency
requiring PyInstaller bundle-list maintenance — the httpx CI lesson):

- **Windows**: DXGI adapter enumeration via `ctypes` (`CreateDXGIFactory1` → `EnumAdapters` →
  `GetDesc`: per-GPU name, `DedicatedVideoMemory`, `VendorId`) + `GlobalMemoryStatusEx` for RAM.
  DXGI is vendor-agnostic (NVIDIA/AMD/Intel) where WMI `win32_VideoController` needs WMI tooling
  and `nvidia-smi` is vendor-specific.
- **Linux**: `/proc/meminfo` for RAM; `nvidia-smi --query-gpu=...` (5 s bounded) when present —
  absent ⇒ empty GPU list, not an error. AMD/Intel are not probed on Linux in V0.1 (reported as
  "nothing detected", never as "zero GPUs exist").
- **Disk**: `shutil.disk_usage` on the model directory (free bytes only; never the path itself).
- **CPU**: `platform.processor()` (empty on some Windows installs ⇒ unknown) + `os.cpu_count()`.

Results are cached to `<data_dir>/hardware.json` (JSON, rewritten on the explicit
`POST /hardware/refresh` re-detect; machine facts do not belong in the assessment DB — keeps
migration 0006 minimal). `GET /hardware` serves the cache structurally when fresh (never
contacts anything); refresh is always the explicit POST (`USER_FLOWS.md` §2.4).

Limitations recorded honestly: the Windows DXGI path is fixture-verified until the owner's
Windows machine runs it (SPRINTS.md exit criterion); multi-GPU detection cannot be validated on a
single-GPU-or-less dev box (synthetic fixtures only); `nvidia-smi` VRAM values are driver-reported
totals, not free-at-runtime.

## 5. Recommendation Math (Sprint 6 contract — the exact formulas S6-06 implements)

Deterministic and unit-tested on synthetic fixtures (`DECISIONS.md` D-023 combined-footprint rule,
PD-026). Headroom constants: `USABLE_RAM_FRACTION = 0.7` (OS/service/evidence workloads),
`USABLE_VRAM_FRACTION = 0.9` (driver-reported totals overstate usable VRAM),
`RECOMMENDED_HEADROOM_FACTOR = 1.5` ("recommended" = total×1.5 still fits the usable budget),
`MIN_DISK_MARGIN_BYTES = 1 GiB`.

**Footprint estimate** for an entry at a context size
(`context_tokens` defaults to the entry's `curated_context_tokens`):

```
weights = Σ files[].size_bytes
kv      = footprint.kv_cache_bytes_per_token × context_tokens
total   = weights + kv + footprint.runtime_overhead_bytes
```

**Hardware budget:** CPU path `budget = ram_total_bytes × 0.7` — RAM *available* bytes are
deliberately **not** used (available RAM fluctuates; feasibility must be stable across a launch).
GPU path: `gpu_budget_total = Σ(gpu.vram_bytes) × 0.9`, `gpu_budget_per_gpu = min(gpu.vram_bytes) × 0.9`.

**CPU verdict:** `total ≤ budget` → feasible; `total × 1.5 ≤ budget` → recommended; else
infeasible (`insufficient_ram`). **Disk gate:** `total_files_bytes + 1 GiB ≤ model_dir_free_bytes`
else infeasible (`insufficient_disk`) regardless of memory. **Any needed input `None` → verdict
`unknown` (`hardware_unknown`), never `infeasible`.**

**GPU verdict (llama.cpp full-offload model):** if `total ≤ gpu_budget_total` **and**
`total / gpu_count ≤ gpu_budget_per_gpu` → feasible/recommended on GPU (`insufficient_vram` /
`exceeds_per_gpu_share` otherwise, then fall back to the CPU-path evaluation; the verdict reports
whichever path fits, GPU preferred). This is the tensor-split reality: weights+KV distribute
across GPUs proportionally, but **each GPU must hold its share — the aggregate is NOT one
contiguous pool** (PD-026). Single-layer granularity (one layer exceeding one GPU) is not parsed
in V0.1; the per-GPU-share check is the conservative proxy.

**Combined pair (D-023):** `combined = estimate(gen) + estimate(emb)`. CPU: `combined ≤ budget` →
`simultaneous`; else if **each** individually fits → `sequential_only` (documented fallback: load
the embedding model for indexing/retrieval, swap to the generation model for evaluation — the
manager's existing stop-then-start switch; never silent); else `infeasible`. The GPU path mirrors
the same two-check form.

**Recommended gate:** final `verdict = "recommended"` only when feasibility passes **and**
`entry.recommended` is true; a feasibility pass on `recommended: false` yields `feasible` with
reason `curator_withheld` (the honest no-ceiling behavior).

`GET /recommendations` evaluates every entry for every role it declares plus all gen×emb pairs;
if that product ever exceeds 25, it restricts to `pairing_default` entries (documented guard).
`GET /recommendations/profile/<id>` runs the same math on a synthetic profile (§3.3).

## 6. Model Download (Sprint 6 contract)

- **User-initiated only; never automatic.** Explicit user approval before any transfer starts:
  the UI confirmation shows source URL, revision, per-file sizes and SHA-256s; the approving
  `POST` itself is the approval record. Nothing downloads at startup or at catalog browse time.
- **Verification fail-closed before any registration:** streamed per-file SHA-256 is checked
  against the manifest (catalog) or the user-supplied expected checksum (URL import); size is
  checked exactly. The `.part` file is renamed to its final name **only after** verification; on
  any mismatch both the `.part` and any final file are deleted and **nothing is registered**.
- **Resumable** via HTTP `Range` with `.part` files (a server answering `200` instead of `206`
  restarts from zero; the digest is resumed by pre-hashing existing part bytes).
- **Progress pollable** (`GET /model-catalog/downloads/{id}` — structural: ids, filenames, byte
  counts only); **one active download at a time** in V0.1.
- **Destination:** only `resolve_model_dir(settings)`; only declared GGUF files are ever
  requested; repository scripts are **never** fetched, let alone executed (PD-026).
- **Privacy (PD-026):** downloads are GETs only, to https URLs taken from the in-repo manifest or
  user-supplied for URL imports; **no evidence, assessment, or telemetry data ever rides a
  download request**. Redirects to `http://` are refused (no downgrade); the integrity control is
  the checksum, the transport control is https-only (HF CDN redirect targets vary and are not
  allowlisted — documented rather than brittle).
- **Approval for URL imports (elevated trust):** a direct-URL import either supplies an expected
  SHA-256 (enforced; `verification: "user_checksum"`) or requires an explicit user acknowledgment
  that the source is unverified (`verification: "none"`; computed hash recorded and displayed).
  Missing both → refused (`expected_checksum_required`).

**Sprint 6 error codes** (added to `model_runtime/errors.py` across S6-04/S6-05; every code has
exactly one fixed message; no code message ever contains a path, URL query, filename, or
credential — T-10; a computed SHA-256 of a downloaded/imported file is structural metadata and may
appear in responses/logs):

| Tuple | Codes |
|---|---|
| `DOWNLOAD_ERROR_CODES` | `download_active`, `download_not_found`, `download_checksum_mismatch`, `download_size_mismatch`, `download_network_error`, `download_http_error`, `download_url_unsupported`, `disk_write_failed`, `disk_space_insufficient`, `expected_checksum_mismatch`, `expected_checksum_required` |
| `CATALOG_ERROR_CODES` | `catalog_entry_unknown`, `catalog_unavailable`, `profile_unknown` |
| `REGISTRY_ERROR_CODES` | `registry_endpoint_invalid`, `registry_endpoint_not_loopback`, `registry_endpoint_unreachable`, `registry_duplicate`, `endpoint_not_found`, `endpoint_attached` |

(`download_cancelled` is a download *state*, not an error — kept out of the tuple.) Route status
mapping: 422 validation-class, 404 unknown-class, 409 conflict-class (`download_active`,
`endpoint_attached`), 502 unreachable/HTTP-failure class, 507 `disk_space_insufficient`. All
members are also exported via the combined `SPRINT6_ERROR_CODES` (the test that pins message
coverage).

## 7. GGUF Import and the User-Managed Registry

User can point the app at an existing local GGUF file instead of downloading. Per `SECURITY.md`
T-08, imported files have **no checksum verification against a known-good manifest** (there is
nothing to verify against), so the UI must clearly warn that an imported model's provenance is
not verified by the app, distinct from a catalog download.

**Registry distinction (Sprint 6):** local imports, direct URL imports (§6, `url_import`), and
registered external loopback inference servers all belong to the **user-managed registry**
(§3.1), persisted with `origin`/`verification` and badged in the UI. The local-import warning
stays exactly as it is; the `url_import` warning is **stronger** (an arbitrary remote source is a
larger supply-chain surface than a file the user already has locally), and an unverified URL
import additionally requires the explicit elevated-trust acknowledgment of §6. Catalog downloads
are the only path that arrives pre-verified (`verification: "catalog_manifest"`).

## 8. Model Start / Stop / Switching

- Start: launch `LlamaCppProvider` subprocess(es) for the selected generation and/or embedding
  model; confirm via `health_check()` before the pipeline is allowed to use each.
- Single active model **per capability** at a time (`DECISIONS.md` D-011, narrowing the original
  D-008 "single active model" statement): at most one generation provider and at most one embedding
  provider are running simultaneously — not one model process total. If the same model backs both
  capabilities, that's one process; if not, it's two. Either way, V0.1 does not run a fleet of
  concurrently-serving models, and switching the generation model does not require restarting the
  embedding model (or vice versa).
- Stop: on app exit, and when the user explicitly switches a model for a given capability (stop
  that capability's current provider before starting its replacement).
- Switching: requires stopping the current provider instance for that capability and starting a
  new one; in-flight pipeline work should be allowed to fail cleanly rather than silently pointing
  at the new model mid-evaluation.

## 9. Health Checking

`health_check()` must be capability-specific (`DECISIONS.md` D-023) — a generation-only or
embedding-only provider must never be health-checked via a call it doesn't support:

- `GenerationCapable.health_check()` performs a minimal, bounded real `generate()` call and
  confirms a well-formed response — not just "process exists"; a hung subprocess that accepts
  connections but never responds must be detectable.
- `EmbeddingCapable.health_check()` performs a minimal `embed()` call on a fixed short string and
  confirms the returned vector is finite (no NaN/Inf values) and has the dimensionality expected for
  the loaded model.

Used at: model start confirmation (per capability), and optionally a periodic/on-demand check
surfaced in Settings.

## 10. Localhost Security and Model-Server Authentication

The llama.cpp server (if run in server mode) must bind to `127.0.0.1` only, exactly like the
FastAPI service (`SECURITY.md` T-09). No LAN or public interface binding in V0.1 under any
configuration.

**Localhost binding is not authentication.** An earlier draft of this document claimed that
binding alone was sufficient because the model server "has exactly one intended caller" (FastAPI).
That is incorrect: any other local process can open a TCP connection to a `127.0.0.1`-bound port
and issue requests directly, regardless of who the design *intends* to be calling it — "intended
caller" is not an enforcement mechanism (`DECISIONS.md` D-017).

The model-runtime server therefore requires its own independently generated credential, separate
from the D-009 frontend↔FastAPI session token:

- FastAPI generates a fresh random credential each time it starts a model-runtime instance, and
  holds it entirely within the backend process boundary — it is **never** sent to the frontend and
  never leaves local process space.
- Every inference request, and any administrative/introspection endpoint, must present this
  credential; requests without it are rejected before any processing. A pure liveness-only health
  endpoint may be explicitly exempted if the runtime distinguishes one from inference-capable
  endpoints — document which endpoints are exempt, don't assume.
- The credential is passed to the child at spawn in a child-scoped environment variable, or through
  a private inherited handle supported by the runtime — never a CLI argument,
  never logged (`SECURITY.md` T-10).
- If llama.cpp's server mode has no native per-request auth, FastAPI fronts it with a thin
  authenticating wrapper in the same process boundary so that no unauthenticated path to the model
  server exists, even as a side channel around the wrapper.

Binding to loopback remains required as defense-in-depth, but is no longer described as sufficient
on its own — see `SECURITY.md` T-23.

## 11. Offline Mode

Once a model is downloaded/imported and verified, all `ModelProvider` operations (`generate`,
`embed`, `health_check`) must function with external network access blocked; authenticated loopback
IPC remains permitted. Only §6 (download) and catalog
browsing require network access, and both are clearly distinguished in the UI from
fully-offline-capable actions per `ARCHITECTURE.md` §11.

See also: `AI_PIPELINE.md` for how `ModelProvider` is used, `SECURITY.md` T-08/T-09 for the
threats this design addresses.
