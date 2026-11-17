"""OS-allocated loopback port for one llama-server launch (S5-02).

The kernel picks the port, so two concurrent launches never collide by
construction. Residual risk (Sprint 5 risk register #5): between closing
the probe socket and the child binding the same port, another local
process could take it -- an impostor is useless without the per-launch
credential (D-017/T-23), so the exposure is availability-only; the
provider bounds even that with ``START_PORT_ATTEMPTS`` retries on
``start_failed``.
"""

from __future__ import annotations

import socket

from .args import BIND_HOST


def allocate_loopback_port() -> int:
    """Ask the kernel for a free loopback port (bind, read, close)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((BIND_HOST, 0))
        return int(sock.getsockname()[1])
