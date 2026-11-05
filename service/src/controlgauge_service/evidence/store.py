"""The local evidence store (Sprint 4, `SECURITY.md` T-05/T-11).

Uploaded files are kept under the per-user data directory, named only by
their assessment id (a canonical UUID the service generated) and SHA-256 --
never by any client-supplied name, so a crafted filename cannot choose a
path. Writes are atomic (temporary file in the same directory, then
rename), so a crash never leaves a half-written file under a final name.

    <data dir>/evidence/<assessment id>/<sha256>.bin
    <data dir>/parser-scratch/<per-parse dir>     (sandbox scratch; wiped at startup)

Nothing here grants the parser sandbox any access: the sandbox only ever
receives an already-open read-only handle to one file.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class EvidenceStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "evidence"
        self.scratch_root = data_dir / "parser-scratch"

    def path_for(self, assessment_id: str, sha256: str) -> Path:
        if str(uuid.UUID(assessment_id)) != assessment_id or not _SHA256.fullmatch(sha256):
            raise ValueError("invalid_store_key")
        return self.root / assessment_id / f"{sha256}.bin"

    def save(self, assessment_id: str, sha256: str, data: bytes) -> Path:
        target = self.path_for(assessment_id, sha256)
        if target.is_file() and target.stat().st_size == len(data):
            return target
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = target.with_name(f".{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "xb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return target

    def reset_scratch(self) -> None:
        """Removes every leftover per-parse scratch directory (an
        interrupted parse cannot clean up after itself)."""
        shutil.rmtree(self.scratch_root, ignore_errors=True)
        self.scratch_root.mkdir(parents=True, exist_ok=True, mode=0o700)
