# ControlGauge — Sprint 5 Development Models

Status: Sprint 5 (S5-01..S5-07). Reproducibility doc for the two **development/validation
models** approved for Sprint 5 by the owner (`docs/planning/DECISION_LOG.md`
PD-023). Both are **development/validation only — not a production decision**
(Sprint 6 curates the production catalog independently).

Everything a fresh developer needs to provision the models and the runtime from
this document alone is below. No machine-specific path is referenced by code:
dev-model filenames and paths may appear only in `docs/` and `tests/`
(enforced by `service/tests/test_model_config.py::test_model_agnostic_source_guard`).

## 1. Models

### Generation — Qwen2.5-7B-Instruct Q4_K_M

- Source: official `Qwen/Qwen2.5-7B-Instruct-GGUF` (Hugging Face), split GGUF,
  2 shards.
- Shards (byte sizes verified 2026-11-17, sizes match the official manifest):

  | File | Bytes | SHA-256 |
  |---|---|---|
  | `qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf` | 3,993,201,344 | `dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db` |
  | `qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf` | 689,872,288 | `539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a` |

- Both shards must sit in the same directory; llama-server is given **shard 1**.

### Embedding — nomic-embed-text-v1.5 Q4_K_M

- Source: official `nomic-ai/nomic-embed-text-v1.5-GGUF` (Hugging Face), single file.
- `nomic-embed-text-v1.5.Q4_K_M.gguf` — 84,106,624 bytes,
  SHA-256 `d4e388894e09cf3816e8b0896d81d265b55e7a9fff9ab03fe8bf4ef5e11295ac`.
- Expected embedding dimensionality: **768**. This number comes from the model
  card — **verify it from the server, don't trust it**: Sprint 5's integration
  tests assert the server-reported dimension against this value and this
  document is corrected if they disagree (S5-04). Server-observed on this dev
  box, 2026-11-17 (S5-02 evidence run): the embeddings response carried
  **768 floats** per vector.

## 2. Runtime — llama.cpp b12480

- Release: **b12480**, commit `7f106b545`, official `ubuntu-x64` CPU build.
- Tarball: `llama-b12480-bin-ubuntu-x64.tar.gz` — 23,689,039 bytes,
  SHA-256 `a68e43192d643097825477fdd6d067b8c3b48333962aac4c12a67c11babacfc0`.
- Extraction layout: the tarball contains a single `llama-b12480/` directory;
  extract it into your models root. The binary is `llama-b12480/llama-server`
  and the bundled `libggml*.so` libraries sit **beside it** — the binary has
  RUNPATH `$ORIGIN`, so they self-resolve and **`LD_LIBRARY_PATH` is not
  needed** (verified on the dev box, 2026-11-17).
- Expected `llama-server --version` output (first line):

  ```
  version: 1.0.0-dev (build 12480, commit 7f106b545)
  built with GNU 11.4.0 for Linux x86_64
  ```

- Flag table used by the provider (verified against this binary's `--help`):
  see `docs/planning/BATCH_JOBS.md` S5-01 / the Sprint 5 spec — `--host`,
  `--port`, `--model`, `--ctx-size`, `--threads`, `--gpu-layers`, `--parallel`,
  `--embedding`, `--no-webui`, `--no-slots`; credential via the `LLAMA_API_KEY`
  environment variable (never `--api-key`, per D-017). Any drift found on a
  future re-pin is recorded here.

## 3. Download / verify steps

`$CONTROLGAUGE_MODELS_ROOT` is a placeholder for wherever you keep models on
your machine (dev-box example: `~/ai-workbench/models/controlgauge/` — an
example only, never referenced by code). The service consumes only the env
vars in §7.

```bash
export CONTROLGAUGE_MODELS_ROOT="$HOME/ai-workbench/models/controlgauge"  # example
mkdir -p "$CONTROLGAUGE_MODELS_ROOT" && cd "$CONTROLGAUGE_MODELS_ROOT"

# Runtime (release b12480, ubuntu-x64 CPU build)
curl -L -o llama-b12480-bin-ubuntu-x64.tar.gz \
  https://github.com/ggml-org/llama.cpp/releases/download/b12480/llama-b12480-bin-ubuntu-x64.tar.gz
sha256sum llama-b12480-bin-ubuntu-x64.tar.gz
#   expect a68e43192d643097825477fdd6d067b8c3b48333962aac4c12a67c11babacfc0
tar -xzf llama-b12480-bin-ubuntu-x64.tar.gz   # -> ./llama-b12480/llama-server

# Generation model (2 shards)
curl -L -o qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf \
  "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
curl -L -o qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf \
  "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf"
sha256sum qwen2.5-7b-instruct-q4_k_m-0000[12]-of-00002.gguf
#   expect dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db (shard 1)
#   expect 539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a (shard 2)

# Embedding model
curl -L -o nomic-embed-text-v1.5.Q4_K_M.gguf \
  "https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.Q4_K_M.gguf"
sha256sum nomic-embed-text-v1.5.Q4_K_M.gguf
#   expect d4e388894e09cf3816e8b0896d81d265b55e7a9fff9ab03fe8bf4ef5e11295ac

./llama-b12480/llama-server --version
#   expect: version: 1.0.0-dev (build 12480, commit 7f106b545)
```

## 4. Split-GGUF note

llama-server loads split GGUFs when given the **first shard**
(`*-00001-of-00002.gguf`); it locates the remaining shards automatically.
Verified empirically at dev time by
`service/tests/test_model_integration.py::test_integration_start_health_stop_generation_model`
(S5-02) — **passing on this dev box, 2026-11-17**. On a fresh machine, until
that test passes locally, treat loading as an assumption, per the Sprint 5
risk register.

## 5. Server auth matrix

Verified 2026-11-17 on this dev box against llama.cpp **b12480** with the real
dev models (embedding server probed directly; the generation child is covered
by `service/tests/test_model_integration.py::test_integration_unauthorized_local_request_rejected`).
`✗ 401` = rejected with HTTP 401 and body
`{"error":{"message":"Invalid API Key","type":"authentication_error",...}}`.
The provider always sends `Authorization: Bearer <credential>` on every
request it makes — harmless on the exempt endpoint, required everywhere else.

| Endpoint | Without credential | With that child's own credential |
|---|---|---|
| `GET /health` | **200 — auth-exempt** (`{"status":"ok"}`; readiness polling relies on this) | 200 |
| `GET /props` | ✗ 401 | 200 |
| `POST /v1/chat/completions` (generation child) | ✗ 401 | 200 |
| `POST /v1/embeddings` (embedding child) | ✗ 401 | 200 |

Per-launch credential isolation (D-017), same run: the two concurrently
running children hold **different** credentials (asserted), and the
generation launch's credential is **rejected by the embedding child with
401** — one launch's credential never opens another child.

Further verified response shapes on this build (S5-02):

- `GET /props` (authenticated) carries `build_info` as a **flat string**,
  e.g. `"b12480-7f106b545"`; `describe()` surfaces exactly that string,
  bounded to 64 chars. The payload's `model_path` field is never read or
  copied (asserted by `test_integration_props_metadata_is_closed`).
- `POST /v1/embeddings` returns the OpenAI-style envelope
  `{model, object, usage: {prompt_tokens, total_tokens},
  data: [{embedding: [<768 floats>], index, object}]}` — no `timings` block
  on this endpoint.
- Flag drift check (args builder vs the §2 table): the S5-02 builder emits
  exactly `--host 127.0.0.1 --port <port> --model <path> --ctx-size 2048
  --parallel 1 --no-webui --no-slots --embedding` for the embedding config,
  omitting `--threads`/`-ngl` while unset — **no drift**.

### 5.1 Structured generation (S5-03 evidence, 2026-11-17)

- **`response_format` nesting verified**: b12480 accepts the OpenAI-style
  `{"type": "json_schema", "json_schema": {"name": ..., "schema": {...}}}`
  on `POST /v1/chat/completions` (chat template applied). The documented
  `/completion` fallback was **not needed**; the provider's single
  `generate()` method stands.
- **Grammar = shape, not range** (found live): b12480's `json_schema`
  grammar enforces the enum literals, object/array structure, and the
  *shape* of a number — but **not numeric `minimum`/`maximum`**. Observed
  on the real model: a free-invented `confidence` of
  `0.9555555555555556E1000000000000000` passes the grammar (valid JSON
  number shape) and parses to `inf`, which deterministic validation then
  rejects with `schema_validation_failed`. Across repeated free-form
  (unpinned-value) runs, overflowing-exponent answers showed up roughly
  **1 in 5** — so ranged schemas on this binary should expect a
  deterministic-validation rejection rate of that order and budget a
  retry accordingly. Division of labor is therefore: **sampler enforces
  shape; deterministic validation (B2) enforces ranges and every other
  semantic constraint.** Sprint 6's pipeline schemas should not rely on
  grammar-level numeric ranges.
- **Sampler enforcement vs semantic quality (PD-023)**: N=5 runs of a
  deliberately nonsensical prompt ("how the number seven migrates for the
  winter, in iambic pentameter") under the enum grammar produced 5/5
  schema-conformant answers — `relationship_type: "REFERENCES"` every
  time, confidences `0.9555…/1.0` — i.e. conformant shape, meaningless
  content. Conformance is the sampler's guarantee; meaningfulness is the
  prompt's.
- **Offline evidence**: the integration module runs every test under the
  `offline_guard` fixture (any non-loopback `connect`/`create_connection`
  attempt raises), and a real structured generation passes under it. The
  optional hard form on Linux:

  ```bash
  unshare -rn sh -c 'ip link set lo up && pytest service/tests/test_model_integration.py'
  ```

  Dev-box outcome (2026-11-17, S5-07): the hard form is **not obtainable
  here** — this machine blocks unprivileged user namespaces via AppArmor
  (`kernel.apparmor_restrict_unprivileged_userns = 1`; `unshare -rn` fails
  with `write failed /proc/self/uid_map: Operation not permitted`). Enabling
  it would be a per-machine system change, so the `offline_guard` run remains
  the offline evidence of record (all 20 integration tests pass under it,
  including a real structured generation and the full exit flow).

### 5.2 Embeddings (S5-04 evidence, 2026-11-17)

- **Dimension verified from the server**: real nomic-embed-text-v1.5 Q4_K_M
  embeddings carry **768 floats** (matches the model card; asserted by
  `test_integration_embedding_finite_and_dimensioned` against the
  conftest-pinned `EXPECTED_EMBEDDING_DIMENSIONS = 768`). Same-process
  determinism verified (identical input → identical vectors); restart
  determinism is a baseline observation, not a guarantee.
- **Normalization / metric (D-025)**: the server runs with the default
  `--embd-normalize 2` = **euclidean/L2**, i.e. vectors are L2-normalized.
  Consequence for consumers: on L2-normalized vectors, **cosine similarity
  ≡ dot product** — Sprint 8 picks the similarity metric
  (`EmbeddingServerConfig.embedding_similarity_metric`, reserved `"cosine"`);
  `embedding_normalization` (reserved `"l2"`) documents what the server does.
- **Model-specific preprocessing is NOT implemented in Sprint 5**: nomic's
  task prefixes (`search_document:` / `search_query:`) are a retrieval-
  quality concern for **Sprint 8** — recorded here so it isn't rediscovered.
  The provider interface stays model-agnostic (no prefixes anywhere in
  code).

## 6. Hardware notes

- Dev box: Linux x86_64. The machine's **three RTX PRO 6000 GPUs are
  detectable** (Sprint 6 hardware detection enumerates them via the real
  `nvidia-smi` path, ≈95.6 GiB each — see §9) but are **occupied by another
  workload**, so the llama-server children here still run CPU-only with
  `-ngl` (GPU layers) left unset; **GPU *inference* deployments remain
  configurable, not tested** (PD-023 owner requirement 2 is satisfied as
  configurability; the Sprint 6 GPU *recommendation* path, by contrast, IS
  exercised on real hardware — §9).
- CPU baseline for context (development box, not a requirement): model load
  of the 7B Q4_K_M takes tens of seconds; see §8 for measured baselines.

## 7. Execution instructions

The service consumes these env vars (prefix `CONTROLGAUGE_`, see
`service/src/controlgauge_service/config.py`):

| Variable | Meaning |
|---|---|
| `CONTROLGAUGE_LLAMA_SERVER` | absolute path of the `llama-server` binary; **required to start any model** — absent → start rejected `runtime_binary_missing` (fail closed; packaged-app provisioning is Sprint 16's concern) |
| `CONTROLGAUGE_MODEL_DIR` | absolute default browse root for the model-import dialog (import accepts any absolute path) |

Integration tests additionally read (see `service/tests/conftest.py` — they
skip cleanly when these are unset, so Windows CI never needs them):

| Variable | Meaning |
|---|---|
| `CONTROLGAUGE_TEST_LLAMA_SERVER` | path of the `llama-server` binary for real-model integration tests |
| `CONTROLGAUGE_TEST_MODEL_GENERATION` | path of the generation model's **first shard** |
| `CONTROLGAUGE_TEST_MODEL_EMBEDDING` | path of the embedding model |

Dev-box example (exported in the dev shell, never committed):

```bash
export CONTROLGAUGE_LLAMA_SERVER="$CONTROLGAUGE_MODELS_ROOT/llama-b12480/llama-server"
export CONTROLGAUGE_TEST_LLAMA_SERVER="$CONTROLGAUGE_LLAMA_SERVER"
export CONTROLGAUGE_TEST_MODEL_GENERATION="$CONTROLGAUGE_MODELS_ROOT/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
export CONTROLGAUGE_TEST_MODEL_EMBEDDING="$CONTROLGAUGE_MODELS_ROOT/nomic-embed-text-v1.5.Q4_K_M.gguf"
```

## 8. Performance baselines

Recorded 2026-11-17 by
`service/tests/test_model_integration.py::test_integration_sprint5_exit_flow_offline`,
which prints this table to the test log on every run (`pytest -s`).
**Development baselines on this dev box only — NOT acceptance thresholds**
(they exist to inform Sprint 6's hardware-recommendation thresholds, never to
gate anything). **GPU deployments are untested here** — CPU-only box, `-ngl`
unset (§6).

Environment:

- Binary: llama.cpp **b12480**, commit `7f106b545` (ubuntu-x64 CPU build, §2).
- Models (SHA-256 from §1): generation
  `qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf`
  `dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db` with
  shard 2 `539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a`;
  embedding `nomic-embed-text-v1.5.Q4_K_M.gguf`
  `d4e388894e09cf3816e8b0896d81d265b55e7a9fff9ab03fe8bf4ef5e11295ac`.
- CPU: `AMD Ryzen Threadripper PRO 7985WX 64-Cores` — x86_64, 128 logical
  CPUs, max clock 5367 MHz (`lscpu`), CPU-only inference.

Method (Sprint 5 spec, adapted to what b12480 actually exposes): one warm-up
generation excluded, then 5 measured runs of one fixed prompt under
`NESTED_SCHEMA`, then 10 measured embeds of one fixed short input. The
measured runs ride a **warm prompt cache** (b12480 reports `cache_n > 0` on
repeats), so these are warm-cache numbers. Generation rows read b12480's
`timings` block (`prompt_ms`/`predicted_ms`) — verified present on
`/v1/chat/completions` — plus client-side wall ms; the **embeddings endpoint
exposes no `timings`** (its usage carries only `prompt_tokens`/`total_tokens`),
so embedding latency is client-side wall ms. Memory is the child's `VmRSS`
from `/proc/<pid>/status` right after load (Linux; **not captured on Windows**
— no Windows dev box in Sprint 5).

Structured generation (7B Q4_K_M, ctx 8192, defaults of §S5-01):

| run | wall_ms | prompt_tokens | output_tokens | prompt_ms | predicted_ms | tok/s (server) | tok/s (client) |
|---|---|---|---|---|---|---|---|
| 1 | 1273.8 | 63 | 58 | 33.8 | 1238.1 | 46.8 | 45.5 |
| 2 | 1273.2 | 63 | 58 | 33.9 | 1237.4 | 46.9 | 45.6 |
| 3 | 1293.2 | 63 | 58 | 36.8 | 1254.4 | 46.2 | 44.9 |
| 4 | 1273.9 | 63 | 58 | 34.2 | 1237.8 | 46.9 | 45.5 |
| 5 | 1283.2 | 63 | 58 | 34.1 | 1247.2 | 46.5 | 45.2 |

`tok/s (server)` = `output_tokens / (predicted_ms / 1000)` (server-side
decode timing); `tok/s (client)` = `output_tokens / wall` — what the service
actually waits for, HTTP overhead included; use that one when budgeting UI
timeouts.

Embedding latency (nomic-embed-text-v1.5 Q4_K_M, client-side wall ms, n=10):
3.9, 4.0, 3.9, 3.9, 3.9, 3.9, 3.9, 3.8, 3.9, 3.8 — **mean 3.9 ms**.

Child memory (`VmRSS` after load, from `/proc/<pid>/status`, Linux):

| child | VmRSS after load |
|---|---|
| generation (Qwen2.5-7B Q4_K_M, ctx 8192) | 7928.7 MiB |
| embedding (nomic-embed-text-v1.5 Q4_K_M, ctx 2048) | 158.2 MiB |

Reading notes for Sprint 6 (context, not thresholds): warm structured answers
of ~60 output tokens cost ~1.3 s end-to-end on this CPU (~46 tok/s decode);
the generation child's ~7.9 GiB resident set (mapped weights + preallocated
8192-token KV cache) is the per-instance RAM number to budget; restart
determinism of these numbers is not guaranteed (fresh box, cold page cache,
or a binary re-pin all shift them).

## 9. Sprint 6 exit-validation evidence

Recorded 2026-11-23 by `service/tests/test_integration_sprint6_exit_flow.py`
(S6-08; runs with the dev gates set, offline-guarded — loopback + local
probes only) and `service/tests/test_download_integration.py` (the
real-network download E2E). This is the evidence behind the SPRINTS.md
Sprint 6 exit criteria; limitations are stated, not smoothed over.

### 9.1 Detected hardware (`GET /hardware`, this box)

```json
{
  "os_name": "linux", "os_version": "7.0.0-42-generic",
  "cpu_model": "x86_64", "cpu_cores_logical": 128,
  "ram_total_bytes": 1081232535552, "ram_available_bytes": 1042004402176,
  "gpus": [
    {"vendor": "nvidia", "name": "NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition",
     "vram_bytes": 102641958912},
    {"vendor": "nvidia", "name": "NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition",
     "vram_bytes": 102641958912},
    {"vendor": "nvidia", "name": "NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition",
     "vram_bytes": 102641958912}
  ],
  "model_dir_free_bytes": 340225961984,
  "detection_complete": true
}
```

(The spec anticipated a no-GPU dev box; the box grew GPUs between Sprint 5
and Sprint 6, so the REAL multi-GPU `nvidia-smi` enumeration is validated
here and the no-GPU case stays covered by the parsing/route fixtures. The
GPUs are occupied by another workload — occupancy ≠ absence. VRAM values
are driver-reported totals, not free-at-runtime.)

### 9.2 Recommendation verdicts (`GET /recommendations` + the three profiles)

Detected hardware decides on the REAL GPU path (aggregate 3×0.9×95.6 GiB and
per-GPU share both clear every entry): all four catalog entries
**`recommended`**, all three pairs **`simultaneous`** (14B total
12,209,335,968 B vs ≈92.4 GiB per-GPU budget). Footprint estimates are
identical across every hardware source:

| entry | total_bytes | detected / workstation-32gb / enterprise-288gb | consumer-cpu |
|---|---|---|---|
| qwen2.5-1.5b-instruct-q4-k-m | 1,996,446,854 | recommended / simultaneous | recommended / simultaneous |
| qwen2.5-7b-instruct-q4-k-m | 6,226,577,504 | recommended / simultaneous | recommended / simultaneous |
| qwen2.5-14b-instruct-q4-k-m | 12,209,335,968 | recommended / simultaneous | **infeasible** `insufficient_ram` |
| nomic-embed-text-v1.5-q4-k-m | 513,603,354 | recommended / simultaneous | recommended / simultaneous |

The honest no-ceiling property holds: the **enterprise-288gb profile
recommends the FULL catalog** (nothing is withheld by hardware — only ever
by the curator gate), workstation-32gb is all recommended, and consumer-cpu
never auto-promotes the 14B while the FAST tier serves it.

Limitations: this box's three GPUs are EQUAL, so the uneven-bank straggler
case (aggregate passes, one GPU's share fails) remains synthetic-fixture
only; the Windows DXGI detection path is fixture-verified until the owner's
Windows machine run (SPRINTS.md exit criterion 1's Windows half).

### 9.3 Download E2E (real network, `CONTROLGAUGE_TEST_ALLOW_DOWNLOAD=1`)

**1/1 PASSED in 16.92 s** (`test_download_integration.py`): the approval
POST starts the real 84,106,624 B nomic fetch from the pinned revision
`0188c9bf…`, the streamed SHA-256 equals the manifest pin
`d4e38889…95ac` fail-closed before registration, `model_file` + `model_source`
rows record `origin=catalog_download` / `verification=catalog_manifest`, the
downloaded file starts and its health check is a real 768-dim embed, stop is
clean, and a rerun takes the duplicate path. Connect auditor: loopback +
`*.huggingface.co`/`*.hf.co` only.

### 9.4 Exit-flow composition (offline-guarded)

**3/3 PASSED in 1.81 s** (`test_integration_sprint6_exit_flow.py`, one
fresh app): detection → detected + three profile reports with the no-ceiling
assertions → the catalog embedding entry registered with the manifest pin
ENFORCED (`expected_sha256` — proving the dev file byte-identical to the
catalog download; catalog `installed: true` via the S6-05 sha-coverage
lookup) → start → real embed health → stop → external endpoint
register(probe=real generation)/attach/structured generation/health/stop
against a loopback stub (the real-engine round trip is S6-05's recorded
integration test).
