"""S5-01: the model-runtime configuration contract.

Model-independent tests (run on Windows CI): the frozen server-config
dataclasses carry no model identity and match the documented defaults,
the ``Settings`` absolute-path contract holds, ``resolve_model_dir``
mirrors ``storage.default_data_dir``, and no source tree names a
dev-model file (PD-023 requirement 1 enforcement).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from controlgauge_service import storage
from controlgauge_service.config import Settings, resolve_model_dir
from controlgauge_service.model_runtime.config import (
    DEFAULT_EMBED_TIMEOUT_S,
    DEFAULT_HEALTH_TIMEOUT_EMBEDDING_S,
    DEFAULT_HEALTH_TIMEOUT_GENERATION_S,
    DEFAULT_START_TIMEOUT_EMBEDDING_S,
    DEFAULT_START_TIMEOUT_GENERATION_S,
    DEFAULT_STOP_GRACE_S,
    EMBEDDING_DEFAULT_CONTEXT_TOKENS,
    EMBEDDING_DEFAULT_MAX_INPUT_CHARS,
    GENERATION_DEFAULT_CONTEXT_TOKENS,
    GENERATION_DEFAULT_MAX_OUTPUT_TOKENS,
    GENERATION_DEFAULT_TEMPERATURE,
    GENERATION_DEFAULT_TOP_K,
    GENERATION_DEFAULT_TOP_P,
    GGUF_MAGIC,
    GGUF_SUPPORTED_VERSIONS,
    MAX_MODEL_FILE_BYTES,
    MIN_MODEL_FILE_BYTES,
    READINESS_POLL_INTERVAL_S,
    START_PORT_ATTEMPTS,
    EmbeddingServerConfig,
    GenerationServerConfig,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Dev-model filename substrings that must never appear in any source
#: tree (PD-023: dev-model identities live only in docs/ and tests/).
_DEV_MODEL_SUBSTRINGS = (b"qwen2.5", b"nomic-embed", b"7b-instruct")
_SOURCE_ROOTS = ("service/src", "app/src", "app/src-tauri/src")


def test_generation_config_defaults_are_model_agnostic() -> None:
    config = GenerationServerConfig(model_path=Path("/models/any.gguf"), model_id="any-model-id")

    assert config.context_tokens == 8192
    assert config.max_output_tokens == 512
    assert config.temperature == 0.2
    assert config.top_p == 0.9
    assert config.top_k == 40
    assert config.gpu_layers is None  # CPU-only default; -ngl never set implicitly
    assert config.threads is None  # server auto-detects
    assert config.parallel_slots == 1
    assert config.start_timeout_s == 600.0
    assert config.health_timeout_s == 60.0
    # No dev-model identity anywhere in the dataclass.
    assert config.model_id == "any-model-id"
    for needle in _DEV_MODEL_SUBSTRINGS:
        assert needle not in repr(config).lower().encode()


def test_embedding_config_defaults_are_model_agnostic() -> None:
    config = EmbeddingServerConfig(model_path=Path("/models/any.gguf"), model_id="any-model-id")

    assert config.context_tokens == 2048
    assert config.max_input_chars == 32_000
    assert config.gpu_layers is None
    assert config.threads is None
    assert config.expected_dimensions is None  # learned on first successful embed
    assert config.embedding_normalization == "l2"  # reserved per D-025
    assert config.embedding_similarity_metric == "cosine"  # reserved per D-025
    assert config.start_timeout_s == 120.0
    assert config.health_timeout_s == 30.0
    for needle in _DEV_MODEL_SUBSTRINGS:
        assert needle not in repr(config).lower().encode()


def test_shared_bounds_match_the_documented_table() -> None:
    """The single-source constants the rest of the runtime reads."""
    assert GENERATION_DEFAULT_CONTEXT_TOKENS == 8192
    assert GENERATION_DEFAULT_MAX_OUTPUT_TOKENS == 512
    assert GENERATION_DEFAULT_TEMPERATURE == 0.2
    assert GENERATION_DEFAULT_TOP_P == 0.9
    assert GENERATION_DEFAULT_TOP_K == 40
    assert EMBEDDING_DEFAULT_CONTEXT_TOKENS == 2048
    assert EMBEDDING_DEFAULT_MAX_INPUT_CHARS == 32_000
    assert DEFAULT_START_TIMEOUT_GENERATION_S == 600.0
    assert DEFAULT_START_TIMEOUT_EMBEDDING_S == 120.0
    assert DEFAULT_HEALTH_TIMEOUT_GENERATION_S == 60.0
    assert DEFAULT_HEALTH_TIMEOUT_EMBEDDING_S == 30.0
    assert DEFAULT_EMBED_TIMEOUT_S == 30.0
    assert DEFAULT_STOP_GRACE_S == 5.0
    assert READINESS_POLL_INTERVAL_S == 0.25
    assert START_PORT_ATTEMPTS == 3
    assert MAX_MODEL_FILE_BYTES == 256 * 1024**3
    assert MIN_MODEL_FILE_BYTES == 4096
    assert GGUF_MAGIC == b"GGUF"
    assert GGUF_SUPPORTED_VERSIONS == (2, 3)


def test_settings_resolve_llama_server_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "llama-server"
    monkeypatch.setenv("CONTROLGAUGE_LLAMA_SERVER", str(binary))

    settings = Settings()

    assert settings.llama_server == binary


def test_settings_llama_server_relative_path_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(llama_server="relative/llama-server")


def test_settings_model_dir_relative_path_rejected() -> None:
    """Same absolute-path contract as `llama_server` (S5-01)."""
    with pytest.raises(ValidationError):
        Settings(model_dir="relative/models")


def test_settings_model_paths_default_to_none() -> None:
    settings = Settings()

    assert settings.llama_server is None
    assert settings.model_dir is None


def test_resolve_model_dir_explicit_overrides_default() -> None:
    explicit = Path(os.path.abspath("/srv/controlgauge-models"))

    assert resolve_model_dir(Settings(model_dir=explicit)) == explicit


def test_resolve_model_dir_defaults_to_data_dir_models() -> None:
    settings = Settings()

    assert resolve_model_dir(settings) == storage.default_data_dir() / "models"


def test_model_agnostic_source_guard() -> None:
    """PD-023 req 1: dev-model filenames appear only in docs/ and tests/.

    Scans the three source trees byte-wise (case-insensitively) for the
    dev-model filename substrings; a hit means model identity leaked into
    code and this test fails.
    """
    offenders: list[str] = []
    for rel_root in _SOURCE_ROOTS:
        for path in sorted((REPO_ROOT / rel_root).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            data = path.read_bytes().lower()
            for needle in _DEV_MODEL_SUBSTRINGS:
                if needle in data:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {needle.decode()}")
    assert offenders == []
