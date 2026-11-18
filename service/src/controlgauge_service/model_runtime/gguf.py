"""GGUF head sniffing for untrusted model-file imports (S5-05, T-08).

An imported model file is **untrusted data**: this module reads exactly the
first bytes needed to check the container shape (the 4-byte ``GGUF`` magic
and the 4-byte little-endian version), plus the file's size from ``stat``.
The content is never parsed further, never executed, and never logged --
the fixed error codes below are the only thing an import refusal reveals
(the file's path and name are equally absent; see ``routes/models.py``).

Sniffing only decides *whether to hand the file to llama-server at all*.
It cannot vouch for the file's provenance or safety: llama-server remains
a full-privilege native parser of the untrusted file (the accepted residual
in the Sprint 5 risk register, consistent with ``MODEL_RUNTIME.md`` §7 /
``SECURITY.md`` T-08; mitigations = the fixed import warning + Sprint 6
checksum verification).
"""

from __future__ import annotations

import struct

from . import config
from .errors import ModelRuntimeError

#: magic (4) + version (4) -- the only bytes this module ever reads.
_HEAD_BYTES = 8
_VERSION_OFFSET = 4
_VERSION_STRUCT = struct.Struct("<I")


def validate_gguf_head(head: bytes, size: int) -> None:
    """Check an untrusted file's leading bytes and size against the
    supported GGUF container shape. Raises ``ModelRuntimeError`` with a
    fixed code: ``model_file_too_small`` / ``model_file_too_large`` (size
    bounds from ``config.py``), ``invalid_gguf_magic``, or
    ``invalid_gguf_version``. Raises ``ValueError`` if ``head`` is shorter
    than the sniff requires (a caller bug, not an untrusted-input case --
    the caller reads exactly ``_HEAD_BYTES``).
    """
    if size < config.MIN_MODEL_FILE_BYTES:
        raise ModelRuntimeError("model_file_too_small")
    if size > config.MAX_MODEL_FILE_BYTES:
        raise ModelRuntimeError("model_file_too_large")
    if len(head) < _HEAD_BYTES:
        raise ValueError("gguf head sniff requires at least 8 bytes")
    if head[: len(config.GGUF_MAGIC)] != config.GGUF_MAGIC:
        raise ModelRuntimeError("invalid_gguf_magic")
    (version,) = _VERSION_STRUCT.unpack_from(head, _VERSION_OFFSET)
    if version not in config.GGUF_SUPPORTED_VERSIONS:
        raise ModelRuntimeError("invalid_gguf_version")
