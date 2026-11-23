"""S5-02: llama-server argv and the allowlisted child environment.

Model-independent (run on Windows CI). The credential must never appear
in argv (D-017) and the child environment must carry nothing but the
allowlist plus the credential -- no ``CONTROLGAUGE_*`` secret may leak
into the child.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from controlgauge_service.model_runtime.args import (
    _ENV_ALLOWLIST,
    CREDENTIAL_ENV_VAR,
    build_child_env,
    build_server_args,
)
from controlgauge_service.model_runtime.config import EmbeddingServerConfig, GenerationServerConfig

_PORT = 51999


def test_build_server_args_generation_minimal(tmp_path: Path) -> None:
    config = GenerationServerConfig(model_path=tmp_path / "model.gguf", model_id="any")

    args = build_server_args(config, _PORT)

    assert args == [
        "--host",
        "127.0.0.1",
        "--port",
        str(_PORT),
        "--model",
        str(tmp_path / "model.gguf"),
        "--ctx-size",
        "8192",
        "--parallel",
        "1",
        "--no-webui",
        "--no-slots",
    ]


def test_build_server_args_embedding_includes_embedding_flag(tmp_path: Path) -> None:
    config = EmbeddingServerConfig(model_path=tmp_path / "model.gguf", model_id="any")

    args = build_server_args(config, _PORT)

    assert "--embedding" in args
    assert args[args.index("--ctx-size") + 1] == "2048"


def test_build_server_args_omits_unset_gpu_and_threads(tmp_path: Path) -> None:
    config = GenerationServerConfig(model_path=tmp_path / "model.gguf", model_id="any")

    args = build_server_args(config, _PORT)

    assert "-ngl" not in args
    assert "--threads" not in args


def test_build_server_args_includes_set_gpu_and_threads(tmp_path: Path) -> None:
    config = GenerationServerConfig(
        model_path=tmp_path / "model.gguf", model_id="any", gpu_layers=12, threads=4
    )

    args = build_server_args(config, _PORT)

    assert args[args.index("-ngl") + 1] == "12"
    assert args[args.index("--threads") + 1] == "4"


def test_build_server_args_binds_loopback_only(tmp_path: Path) -> None:
    config = GenerationServerConfig(model_path=tmp_path / "model.gguf", model_id="any")

    args = build_server_args(config, _PORT)

    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert "0.0.0.0" not in args
    assert "::" not in args


def test_build_server_args_never_carries_the_credential(tmp_path: Path) -> None:
    config = GenerationServerConfig(model_path=tmp_path / "model.gguf", model_id="any")

    args = build_server_args(config, _PORT)

    assert "--api-key" not in args
    assert CREDENTIAL_ENV_VAR not in args


def test_build_child_env_contains_only_allowlist_plus_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/tester")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    # Secrets and unrelated variables that must NOT reach the child:
    monkeypatch.setenv("CONTROLGAUGE_SESSION_TOKEN", "test-only-session-token-abcdef12")
    monkeypatch.setenv("CONTROLGAUGE_DATA_DIR", "/somewhere/private")
    monkeypatch.setenv("LLAMA_ARG_MODEL", "/injected/model.gguf")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-ours")

    env = build_child_env("test-credential-value")

    assert env[CREDENTIAL_ENV_VAR] == "test-credential-value"
    # The invariant is the ALLOWLIST bound, not a POSIX key set: on a
    # Windows runner the allowlisted SystemRoot/ComSpec/PATHEXT/TEMP/TMP
    # are set in this process's environment and are correctly copied
    # (a healthy Windows child needs them -- args.py module docs), so
    # the exact key set is platform-dependent. What must hold on every
    # platform: nothing outside the allowlist plus the credential, and
    # every variable this test set explicitly does arrive.
    assert set(env) <= set(_ENV_ALLOWLIST) | {CREDENTIAL_ENV_VAR}
    assert {"HOME", "LANG", "PATH", "TMPDIR", CREDENTIAL_ENV_VAR} <= set(env)
    assert not any(key.startswith("CONTROLGAUGE_") for key in env)
    assert "LLAMA_ARG_MODEL" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_build_child_env_credential_present_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    env = build_child_env("test-credential-value")

    assert list(env.values()).count("test-credential-value") == 1
    assert sum(1 for key in env if key == CREDENTIAL_ENV_VAR) == 1


def test_build_child_env_survives_empty_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stripped environment still yields a usable child env with the
    credential. The POSIX keys are deleted; the Windows keys are NOT
    strippable this way and correctly arrive via the allowlist on a
    Windows runner (a healthy Windows child needs them) -- so the
    platform-independent assertion is the allowlist bound, with the
    exact POSIX set pinned on POSIX only."""
    for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP"):
        monkeypatch.delenv(key, raising=False)

    env = build_child_env("test-credential-value")

    assert env[CREDENTIAL_ENV_VAR] == "test-credential-value"
    assert set(env) <= set(_ENV_ALLOWLIST) | {CREDENTIAL_ENV_VAR}
    if sys.platform == "win32":
        # What a healthy Windows child requires arrives even here.
        assert {"SystemRoot", "ComSpec"} <= set(env)
    else:
        assert set(env) == {CREDENTIAL_ENV_VAR}
