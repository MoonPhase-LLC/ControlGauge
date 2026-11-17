"""llama-server command line and child environment (S5-02, PD-024/D-017).

Two pure functions, verified against the pinned binary's ``--help`` (see
``docs/DEV_MODELS.md`` for the flag table and any drift found on re-pin):

- ``build_server_args``: everything the server needs EXCEPT the
  credential -- the credential never appears in argv (D-017; it travels
  via the child-scoped ``LLAMA_API_KEY`` environment variable, and the
  model path in argv is an accepted, documented local disclosure).
- ``build_child_env``: an **allowlisted** child environment plus the
  credential. Never a wholesale ``os.environ`` copy: the service process
  carries secrets (``CONTROLGAUGE_SESSION_TOKEN``) that must not leak
  into a child whose environment is readable via other means.

``LD_LIBRARY_PATH`` is deliberately absent from the allowlist: the pinned
b12480 binary has RUNPATH ``$ORIGIN`` and resolves its bundled
``libggml*.so`` files beside itself (verified on the dev box,
``docs/DEV_MODELS.md`` §2).
"""

from __future__ import annotations

import os

from .config import EmbeddingServerConfig, GenerationServerConfig

#: The credential's env-var name in the child (D-017; verified present in
#: the pinned binary -- `--api-key` exists as the argv fallback and is
#: forbidden here).
CREDENTIAL_ENV_VAR = "LLAMA_API_KEY"

#: The only variables the child receives from this process's environment.
#: Windows needs ``SystemRoot``/``ComSpec``/``PATHEXT`` for a healthy
#: child process; POSIX needs ``PATH``/``HOME``/locale/TMP variables.
#: Anything else (every ``CONTROLGAUGE_*`` secret included) is dropped.
_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SystemRoot",
    "SYSTEMROOT",
    "ComSpec",
    "PATHEXT",
)

#: Loopback-only bind address, always passed explicitly (never rely on
#: the binary default -- flag drift must not loosen the bind).
BIND_HOST = "127.0.0.1"


def build_server_args(
    config: GenerationServerConfig | EmbeddingServerConfig, port: int
) -> list[str]:
    """The full argv tail for one llama-server launch: loopback bind,
    model file, context, single slot, surface reduction (--no-webui,
    --no-slots), ``--embedding`` for embedding configs, and
    ``--threads``/``-ngl`` only when explicitly configured."""
    args = [
        "--host",
        BIND_HOST,
        "--port",
        str(port),
        "--model",
        str(config.model_path),
        "--ctx-size",
        str(config.context_tokens),
        "--parallel",
        "1",
        "--no-webui",
        "--no-slots",
    ]
    if isinstance(config, EmbeddingServerConfig):
        args.append("--embedding")
    if config.threads is not None:
        args.extend(["--threads", str(config.threads)])
    if config.gpu_layers is not None:
        args.extend(["-ngl", str(config.gpu_layers)])
    return args


def build_child_env(credential: str) -> dict[str, str]:
    """Allowlisted environment plus ``LLAMA_API_KEY=<credential>``.

    Only allowlisted variables that are actually set are copied; the
    credential is always present exactly once under its reserved name.
    """
    env: dict[str, str] = {}
    for name in _ENV_ALLOWLIST:
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    env[CREDENTIAL_ENV_VAR] = credential
    return env
