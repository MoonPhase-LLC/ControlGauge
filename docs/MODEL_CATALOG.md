# Model Catalog — provenance and change record

The curated catalog (`models_catalog/catalog.json` + `models_catalog/profiles.json`) is
owner-approved DATA shipped with the app (`docs/MODEL_RUNTIME.md` §3). This file records where
every pin value came from and what changed with each catalog version. Pin values are **never
hand-invented**: each entry's `source.revision` and per-file `size_bytes`/`sha256` are read from
the Hugging Face API response for `GET https://huggingface.co/api/models/<repo>/revision/<sha>?blobs=true`
at curation time, and the env-gated test
`service/tests/test_catalog_matches_huggingface.py` (run with
`CONTROLGAUGE_TEST_ALLOW_DOWNLOAD=1`) re-verifies every recorded value against the live API.

## Provenance — catalog_version 1.0.0 (fetched 2026-11-22)

| Entry | Repo | Revision (main @ fetch) | Files pinned | Card license | Verifier |
|---|---|---|---|---|---|
| `qwen2.5-1.5b-instruct-q4-k-m` | `Qwen/Qwen2.5-1.5B-Instruct-GGUF` | `91cad51170dc346986eccefdc2dd33a9da36ead9` | `qwen2.5-1.5b-instruct-q4_k_m.gguf` — 1,117,320,736 B / `6a1a2eb6…407e` | `apache-2.0` (card + `LICENSE` file) | Jerry (GLM agent), raw API responses archived dev-side |
| `qwen2.5-7b-instruct-q4-k-m` | `Qwen/Qwen2.5-7B-Instruct-GGUF` | `bb5d59e06d9551d752d08b292a50eb208b07ab1f` | `…-00001-of-00002.gguf` — 3,993,201,344 B / `dfce12e3…80db`; `…-00002-of-00002.gguf` — 689,872,288 B / `539cf93f…3d72a` | `apache-2.0` (card + `LICENSE` file) | Jerry; both shard values independently cross-checked against `docs/DEV_MODELS.md` §1 (dev models downloaded and hash-verified 2026-11-17) |
| `qwen2.5-14b-instruct-q4-k-m` | `Qwen/Qwen2.5-14B-Instruct-GGUF` | `b466e1f8c07172155743e8e1307507d8a4f91fbd` | `…-00001-of-00003.gguf` — 3,991,999,872 B / `a09ea5e7…4f68`; `…-00002-of-00003.gguf` — 3,989,373,504 B / `21b9457d…41ae`; `…-00003-of-00003.gguf` — 1,006,737,120 B / `c8d37006…9976` | `apache-2.0` (card + `LICENSE` file) | Jerry |
| `nomic-embed-text-v1.5-q4-k-m` | `nomic-ai/nomic-embed-text-v1.5-GGUF` | `0188c9bf409793f810680a5a431e7b899c46104c` | `nomic-embed-text-v1.5.Q4_K_M.gguf` — 84,106,624 B / `d4e38889…95ac` | `apache-2.0` (card; no separate `LICENSE` file in the repo, so `license_url` points at the model card) | Jerry; size/hash cross-checked against `docs/DEV_MODELS.md` §1 |

Notes recorded honestly:

- The spec expected the 14B GGUF repo to carry the upstream Qwen research license; the card of
  the **GGUF conversion repo** actually declares `apache-2.0` (as do all four curated repos).
  The manifest records what the card says, per the S6-03 contract ("record what the card says").
  The upstream `Qwen/Qwen2.5-14B-Instruct` source-model license is a separate question for a
  future curation pass if entries ever switch to sourcing weights from upstream repos.
- `revision_ref` is `"main"` for all four entries — recorded as the ref the revision was
  resolved from at fetch time. The download URLs embed only the immutable 40-hex revision.
- Footprint numbers (`kv_cache_bytes_per_token`, `runtime_overhead_bytes`) are author-assigned
  estimates with the formula in each entry's `footprint.notes` and the consuming formulas in
  `docs/MODEL_RUNTIME.md` §5 — they are not upstream facts and are not covered by the HF
  match test.
- HF revision-pin drift policy: pinned `resolve/<sha>/` URLs are immutable per commit. If
  upstream deletes a file or the repo, downloads fail with a fixed error code and the entry must
  be re-curated. Manifest updates ride app releases; the owner approves catalog changes per
  `docs/SPRINTS.md` human responsibilities — no auto-update, ever.

## Change record

| catalog_version | Change | Approved |
|---|---|---|
| 1.0.0 | Initial curated catalog: PD-026's four entries (FAST 1.5B / BALANCED 7B / ACCURATE 14B generation + nomic-embed-v1.5 embedding), all `recommended: true`, pairing default = 7B + nomic. | Owner gate PD-026 (2026-11-22) |
