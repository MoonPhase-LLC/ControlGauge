# ControlGauge — Sprint 5 Development Models

Status: Sprint 5 (S5-01, S5-02). Reproducibility doc for the two **development/validation
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
  rejects with `schema_validation_failed`. Division of labor is therefore:
  **sampler enforces shape; deterministic validation (B2) enforces ranges
  and every other semantic constraint.** Sprint 6's pipeline schemas
  should not rely on grammar-level numeric ranges.
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

## 6. Hardware notes

- Dev box: Linux x86_64, **CPU-only** (the machine's GPUs are occupied by
  other workloads) — `-ngl` (GPU layers) is a provider config field but is
  left unset here; **GPU deployments are configurable, not tested** in
  Sprint 5 (PD-023 owner requirement 2 is satisfied as configurability).
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

**Placeholder — filled in S5-07.** Development baselines on this dev box
only; **NOT acceptance thresholds** (they exist to inform Sprint 6's
hardware-recommendation thresholds). Will record: per-run
`prompt_tokens`/`output_tokens`/`prompt_ms`/`predicted_ms` for 5 fixed
structured generations, wall-ms for 10 fixed embeds, and child `VmRSS` after
load, with binary build, model hashes and CPU info.
