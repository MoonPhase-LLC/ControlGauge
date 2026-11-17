"""S5-02: the per-launch model-server credential (D-017/T-23).

Fresh per launch, header-safe, and never carried by argv, descriptors,
health statuses, or provider reprs.
"""

from __future__ import annotations

from pathlib import Path

from controlgauge_service.config import is_valid_session_token_format
from controlgauge_service.model_runtime.args import build_server_args
from controlgauge_service.model_runtime.config import GenerationServerConfig
from controlgauge_service.model_runtime.protocols import HealthStatus, ModelDescriptor
from controlgauge_service.model_runtime.provider import LlamaCppProvider

_PORT = 51998


def _provider(tmp_path: Path) -> LlamaCppProvider:
    config = GenerationServerConfig(model_path=tmp_path / "model.gguf", model_id="any")
    return LlamaCppProvider(config, runtime_binary=tmp_path / "llama-server", role="generation")


def test_credential_is_valid_session_token_format(tmp_path: Path) -> None:
    provider = _provider(tmp_path)

    credential = provider._credential

    assert is_valid_session_token_format(credential)
    # secrets.token_urlsafe(32): 32 bytes -> exactly 43 unpadded base64url chars.
    assert len(credential) == 43


def test_credential_fresh_per_start(tmp_path: Path) -> None:
    """Each launch (each provider the manager constructs) gets its own
    credential -- never a reused one."""
    first = _provider(tmp_path)
    second = _provider(tmp_path)

    assert first._credential != second._credential


def test_credential_never_in_args_or_descriptor(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    credential = provider._credential
    config = GenerationServerConfig(model_path=tmp_path / "model.gguf", model_id="any")

    args = build_server_args(config, _PORT)
    descriptor = ModelDescriptor(
        model_id="any",
        display_name="Any model",
        role="generation",
        context_tokens=8192,
    )
    status = HealthStatus(state="ready", checked_at=None)

    assert credential not in " ".join(args)
    assert credential not in repr(descriptor)
    assert credential not in str(descriptor)
    assert credential not in repr(status)
    assert credential not in str(status)
    assert credential not in repr(provider)
    assert credential not in str(provider)
