"""Model-runtime configuration contract (S5-01, PD-023/PD-024).

Everything that shapes a ``llama-server`` launch lives in these frozen
dataclasses or the module constants below -- **never** as code constants
naming a model. Which model a capability runs is decided at start time
from a persisted ``model_file`` record (S5-05) plus explicit start-time
overrides; the defaults here are capability-level budgets, not model
choices. Dev-model filenames may appear only in ``docs/`` and ``tests/``
(PD-023 requirement 1; enforced by
``tests/test_model_config.py::test_model_agnostic_source_guard``).

Timeouts are sized for the development reality of a 7B model loading on
CPU (tens of seconds for the server to come up, the first generation
after load similarly slow -- prompt-cache warm-up). They are
configurable per start; the defaults here are generous ceilings, not
performance claims (S5-07 records measured baselines).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

GENERATION_DEFAULT_CONTEXT_TOKENS = 8192
GENERATION_DEFAULT_MAX_OUTPUT_TOKENS = 512
GENERATION_DEFAULT_TEMPERATURE = 0.2
GENERATION_DEFAULT_TOP_P = 0.9
GENERATION_DEFAULT_TOP_K = 40
EMBEDDING_DEFAULT_CONTEXT_TOKENS = 2048
EMBEDDING_DEFAULT_MAX_INPUT_CHARS = 32_000
DEFAULT_START_TIMEOUT_GENERATION_S = 600.0
DEFAULT_START_TIMEOUT_EMBEDDING_S = 120.0
DEFAULT_HEALTH_TIMEOUT_GENERATION_S = 60.0
DEFAULT_HEALTH_TIMEOUT_EMBEDDING_S = 30.0
DEFAULT_EMBED_TIMEOUT_S = 30.0
DEFAULT_STOP_GRACE_S = 5.0
READINESS_POLL_INTERVAL_S = 0.25
START_PORT_ATTEMPTS = 3
MAX_MODEL_FILE_BYTES = 256 * 1024**3  # sanity bound; Sprint 6 catalog refines
MIN_MODEL_FILE_BYTES = 4096
GGUF_MAGIC = b"GGUF"
GGUF_SUPPORTED_VERSIONS = (2, 3)


@dataclass(frozen=True)
class GenerationServerConfig:
    """Launch + sampling budget for one generation capability.

    ``model_id`` comes from the model_file record (S5-05) -- never a
    model name in code. ``gpu_layers`` (``-ngl``) is left ``None`` on
    this CPU-only dev box; GPU deployments may set it, but that path is
    untested here (PD-023 requirement 2).
    """

    model_path: Path
    model_id: str  # from the model_file record; never a model name in code
    context_tokens: int = GENERATION_DEFAULT_CONTEXT_TOKENS
    max_output_tokens: int = GENERATION_DEFAULT_MAX_OUTPUT_TOKENS
    temperature: float = GENERATION_DEFAULT_TEMPERATURE
    top_p: float = GENERATION_DEFAULT_TOP_P
    top_k: int = GENERATION_DEFAULT_TOP_K
    gpu_layers: int | None = None  # -ngl; None => omit flag (CPU-only)
    threads: int | None = None  # --threads; None => server auto
    parallel_slots: int = 1
    start_timeout_s: float = DEFAULT_START_TIMEOUT_GENERATION_S
    health_timeout_s: float = DEFAULT_HEALTH_TIMEOUT_GENERATION_S


@dataclass(frozen=True)
class EmbeddingServerConfig:
    """Launch + input bounds for one embedding capability.

    ``expected_dimensions`` starts ``None`` and is pinned after the first
    successful embed (learn-and-pin, S5-04); the manager persists it to
    the model_file row (S5-05). ``embedding_normalization`` /
    ``embedding_similarity_metric`` are the reserved D-025 fields: the
    server's ``--embd-normalize`` default (2 = euclidean/L2) matches
    ``"l2"``, and the similarity metric is a consumer-side concern
    Sprint 8 will pick.
    """

    model_path: Path
    model_id: str
    context_tokens: int = EMBEDDING_DEFAULT_CONTEXT_TOKENS
    gpu_layers: int | None = None
    threads: int | None = None
    max_input_chars: int = EMBEDDING_DEFAULT_MAX_INPUT_CHARS
    expected_dimensions: int | None = None  # pinned after first successful embed (persisted)
    embedding_normalization: str = "l2"  # reserved per D-025; matches --embd-normalize default 2
    embedding_similarity_metric: str = "cosine"  # reserved per D-025
    start_timeout_s: float = DEFAULT_START_TIMEOUT_EMBEDDING_S
    health_timeout_s: float = DEFAULT_HEALTH_TIMEOUT_EMBEDDING_S
