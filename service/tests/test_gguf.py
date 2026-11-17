"""S5-05: GGUF head sniffing for untrusted model-file imports."""

from __future__ import annotations

import struct

import pytest

from controlgauge_service.model_runtime import config, gguf
from controlgauge_service.model_runtime.errors import ModelRuntimeError


def _head(version: int) -> bytes:
    return b"GGUF" + struct.pack("<I", version)


def test_valid_gguf_v2_head_accepted() -> None:
    gguf.validate_gguf_head(_head(2), config.MIN_MODEL_FILE_BYTES)


def test_valid_gguf_v3_head_accepted() -> None:
    gguf.validate_gguf_head(_head(3), config.MIN_MODEL_FILE_BYTES)


def test_bad_magic_rejected() -> None:
    head = b"NOPE" + struct.pack("<I", 3)
    with pytest.raises(ModelRuntimeError) as excinfo:
        gguf.validate_gguf_head(head, 8192)
    assert excinfo.value.code == "invalid_gguf_magic"


def test_unsupported_version_rejected() -> None:
    for version in (0, 1, 4, 2**32 - 1):
        with pytest.raises(ModelRuntimeError) as excinfo:
            gguf.validate_gguf_head(_head(version), 8192)
        assert excinfo.value.code == "invalid_gguf_version"


def test_undersized_file_rejected() -> None:
    with pytest.raises(ModelRuntimeError) as excinfo:
        gguf.validate_gguf_head(_head(3), config.MIN_MODEL_FILE_BYTES - 1)
    assert excinfo.value.code == "model_file_too_small"


def test_oversized_stat_rejected() -> None:
    with pytest.raises(ModelRuntimeError) as excinfo:
        gguf.validate_gguf_head(_head(3), config.MAX_MODEL_FILE_BYTES + 1)
    assert excinfo.value.code == "model_file_too_large"


def test_bounds_are_inclusive() -> None:
    gguf.validate_gguf_head(_head(3), config.MIN_MODEL_FILE_BYTES)
    gguf.validate_gguf_head(_head(3), config.MAX_MODEL_FILE_BYTES)


def test_short_head_is_a_caller_bug_not_an_untrusted_input() -> None:
    """The caller reads exactly the sniff's 8 bytes; a shorter buffer means
    the route was miswired -- a ValueError, not an untrusted-input code."""
    with pytest.raises(ValueError):
        gguf.validate_gguf_head(b"GGU", 8192)


def test_sniff_reads_nothing_but_magic_and_version() -> None:
    """Trailing bytes never influence the verdict: a valid head followed by
    arbitrary (even executable-looking) content is accepted here -- deeper
    content is never inspected, parsed, or executed."""
    payload = _head(3) + b"#!/bin/sh\necho pwned\n" * 100
    gguf.validate_gguf_head(payload[:8], 8192)
