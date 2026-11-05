"""Containment-harness probe (D-022). TEST ONLY -- never shipped.

Run inside the parser sandbox in place of the real worker. Each argv item
is one deliberate escape attempt; the probe prints one JSON object mapping
each attempt to "allowed" or "denied:<ExceptionName>". Tests then assert
the sandbox denied it. Resource-bound probes (`alloc`, `spin`, `flood`,
`sleep`) do not return; the test asserts the sandbox stopped them.

Standard library only, so it runs from any interpreter the sandbox allows.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time


def _attempt(action: str) -> str:
    kind, _, arg = action.partition("=")
    try:
        if kind == "read":
            with open(arg, "rb") as f:
                f.read(1)
        elif kind == "list":
            os.listdir(arg)
        elif kind == "write":
            with open(arg, "wb") as f:
                f.write(b"x")
        elif kind == "connect":
            host, port = arg.rsplit(":", 1)
            with socket.create_connection((host, int(port)), timeout=3):
                pass
        elif kind == "resolve":
            socket.getaddrinfo(arg, 443)
        elif kind == "spawn":
            subprocess.run([sys.executable, "-c", "pass"], check=True, timeout=10)  # noqa: S603
        elif kind == "stdin":
            data = sys.stdin.buffer.read()
            return f"allowed:{len(data)}"
        elif kind == "env":
            return "allowed:" + ",".join(sorted(os.environ))
        elif kind == "alloc":
            hold = []
            while True:
                hold.append(bytearray(64 * 1024 * 1024))
        elif kind == "spin":
            while True:
                pass
        elif kind == "sleep":
            time.sleep(3600)
        elif kind == "flood":
            out = sys.stdout.buffer
            block = b"x" * 65536
            while True:
                out.write(block)
        else:
            return "unknown"
    except Exception as exc:  # noqa: BLE001 - reporting what the OS did
        return f"denied:{type(exc).__name__}"
    return "allowed"


def main() -> None:
    results = {action: _attempt(action) for action in sys.argv[1:]}
    sys.stdout.write(json.dumps(results))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
