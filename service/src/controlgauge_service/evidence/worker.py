"""The parser worker process (Sprint 4). Runs only inside the containment
boundary (`sandbox.py`, D-022).

Contract: the file to parse is on **stdin** (a read-only handle the service
opened); the format is the last argument; one JSON document goes to
**stdout**; stderr is discarded by the sandbox. The worker never receives
a path, never writes anywhere but its scratch directory, and never logs.

Output (validated by the service, which treats it as untrusted):

    {"v": 1, "status": "...", "detail": "<fixed code>|null",
     "parser": "<parser version>",
     "sections": [{"content": "...", "locator": "..."}]}

Any exception becomes `failed` with a fixed category; a traceback (which
could quote document content) is never printed.
"""

from __future__ import annotations

import json
import sys

from . import limits

PROTOCOL_VERSION = 1


def _emit(payload: dict[str, object]) -> None:
    out = sys.stdout.buffer
    out.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    out.flush()


def _read_input() -> bytes | None:
    data = sys.stdin.buffer.read(limits.MAX_FILE_BYTES + 1)
    if len(data) > limits.MAX_FILE_BYTES:
        return None
    return data


def main() -> None:
    fmt = sys.argv[-1] if len(sys.argv) > 1 else ""
    try:
        data = _read_input()
        if data is None:
            _emit({"v": PROTOCOL_VERSION, "status": "failed", "detail": "file_too_large"})
            return
        from .parsers import parse

        result = parse(fmt, data)
        _emit(
            {
                "v": PROTOCOL_VERSION,
                "status": result.status,
                "detail": result.detail,
                "parser": result.parser,
                "sections": [{"content": s.content, "locator": s.locator} for s in result.sections],
            }
        )
    except MemoryError:
        _emit({"v": PROTOCOL_VERSION, "status": "failed", "detail": "memory_limit"})
    except RecursionError:
        _emit({"v": PROTOCOL_VERSION, "status": "failed", "detail": "recursion_limit"})
    except BaseException:  # noqa: BLE001 - fixed category, never a traceback
        try:
            _emit({"v": PROTOCOL_VERSION, "status": "failed", "detail": "parser_error"})
        except BaseException:  # noqa: BLE001, S110
            pass
    finally:
        try:
            sys.stdout.flush()
        except BaseException:  # noqa: BLE001, S110
            pass


if __name__ == "__main__":
    main()
