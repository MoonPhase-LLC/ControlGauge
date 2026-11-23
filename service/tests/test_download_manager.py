"""S6-04: the download manager against a loopback ``http.server`` stub.

Every transport behavior the real HF exchange has -- fresh downloads,
Range resume, Range-ignoring servers, redirect chains, drops, slow
trickles -- is exercised here against KB-scale fixtures on 127.0.0.1
(no large fixtures, no external network; Windows-CI-safe and
offline-guard compatible since every connection is loopback). The
CI lesson is honored structurally: size-bound behaviors are tested with
tiny files, and the one size-gate test injects its answer through the
`shutil.disk_usage` seam.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from controlgauge_service.db import initialize_database
from controlgauge_service.db.models import ModelFile
from controlgauge_service.model_runtime.download import (
    HASH_CHUNK_BYTES,
    DownloadFileSpec,
    DownloadManager,
    DownloadRequest,
    download_file,
    new_download_id,
    validate_user_url,
)
from controlgauge_service.model_runtime.errors import ModelRuntimeError

from .conftest import VALID_TOKEN, _gguf_body, _url

AUTH = {"Authorization": f"Bearer {VALID_TOKEN}"}


def _spec(
    server: Any,
    path: str = "/model.gguf",
    *,
    sha256: str | None = None,
    size_bytes: int | None = None,
) -> DownloadFileSpec:
    """A spec for a stubbed path. Content may be absent (redirect/status
    tests never serve a body); `size_bytes` overrides the declared size
    (the seam for size-gate tests)."""
    content = server.state.files.get(path, b"")
    return DownloadFileSpec(
        filename=path.lstrip("/"),
        url=_url(server, path),
        size_bytes=len(content) if size_bytes is None else size_bytes,
        sha256=sha256 if sha256 is not None else hashlib.sha256(content).hexdigest(),
    )


def _request(
    server: Any,
    tmp_path: Path,
    *,
    path: str = "/model.gguf",
    files: tuple[DownloadFileSpec, ...] | None = None,
    display_name: str = "Stub Model",
    role: str = "generation",
    sha256: str | None = None,
    catalog_model_id: str | None = None,
) -> DownloadRequest:
    return DownloadRequest(
        download_id=new_download_id(),
        display_name=display_name,
        role=role,
        dest_dir=tmp_path / "models",
        files=(files if files is not None else (_spec(server, path, sha256=sha256),)),
        origin="catalog_download",
        catalog_model_id=catalog_model_id,
        source_url=None,
        revision=None,
        license_notice=None,
        verification="catalog_manifest",
    )


def _wait_terminal(manager: DownloadManager, download_id: str, timeout: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.status(download_id)
        if status.state in {"completed", "failed", "cancelled"}:
            return status
        time.sleep(0.02)
    pytest.fail("download did not reach a terminal state")


# --- download_file mechanics ------------------------------------------------------


def test_fresh_download_completes_and_verifies(stub_server: Any, tmp_path: Path) -> None:
    server = stub_server
    server.state.files["/model.gguf"] = _gguf_body(b"fresh")
    final_path, digest = download_file(
        _spec(server), tmp_path / "dest", threading.Event(), lambda _: None
    )
    content = server.state.files["/model.gguf"]
    assert digest == hashlib.sha256(content).hexdigest()
    assert final_path.read_bytes() == content
    assert not (tmp_path / "dest" / "model.gguf.part").exists()


def test_resume_sends_range_and_verifies(stub_server: Any, tmp_path: Path) -> None:
    server = stub_server
    content = server.state.files.setdefault("/model.gguf", _gguf_body(b"resume"))
    part = tmp_path / "dest" / "model.gguf.part"
    part.parent.mkdir(parents=True)
    already = content[: len(content) // 3]
    part.write_bytes(already)
    final_path, digest = download_file(
        _spec(server), tmp_path / "dest", threading.Event(), lambda _: None
    )
    # The Range exchange really happened, from the part's exact size.
    assert server.state.range_headers_seen == [f"bytes={len(already)}-"]
    assert final_path.read_bytes() == content
    assert digest == hashlib.sha256(content).hexdigest()


def test_resume_restart_when_server_ignores_range(stub_server: Any, tmp_path: Path) -> None:
    server = stub_server
    content = server.state.files.setdefault("/model.gguf", _gguf_body(b"ignore"))
    server.state.ignore_range = True
    part = tmp_path / "dest" / "model.gguf.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(content[:100])
    final_path, digest = download_file(
        _spec(server), tmp_path / "dest", threading.Event(), lambda _: None
    )
    # A 200 to a Range request restarts from zero and still verifies.
    assert server.state.range_headers_seen == ["bytes=100-"]
    assert final_path.read_bytes() == content
    assert digest == hashlib.sha256(content).hexdigest()


def test_oversized_part_is_discarded(stub_server: Any, tmp_path: Path) -> None:
    server = stub_server
    content = server.state.files.setdefault("/model.gguf", _gguf_body(b"oversize"))
    part = tmp_path / "dest" / "model.gguf.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(content + b"garbage")
    final_path, digest = download_file(
        _spec(server), tmp_path / "dest", threading.Event(), lambda _: None
    )
    # An oversized part cannot be resumed against: no Range, fresh start.
    assert server.state.range_headers_seen == []
    assert final_path.read_bytes() == content
    assert digest == hashlib.sha256(content).hexdigest()


def test_checksum_mismatch_deletes_part(stub_server: Any, tmp_path: Path) -> None:
    server = stub_server
    server.state.files["/model.gguf"] = _gguf_body(b"actual")
    spec = _spec(server, sha256="ab" * 32)  # a declared digest that cannot match
    with pytest.raises(ModelRuntimeError) as excinfo:
        download_file(spec, tmp_path / "dest", threading.Event(), lambda _: None)
    assert excinfo.value.code == "download_checksum_mismatch"
    # Fail closed: no final file, no part left to poison a later resume.
    assert not (tmp_path / "dest" / "model.gguf").exists()
    assert not (tmp_path / "dest" / "model.gguf.part").exists()


def test_size_mismatch_when_declared_size_differs(stub_server: Any, tmp_path: Path) -> None:
    """The size gate: a stream that completes cleanly but delivers
    something other than the DECLARED size (a padded or lying payload)
    fails `download_size_mismatch` and leaves nothing behind. (A real
    mid-stream truncation surfaces earlier as a transport-level
    `download_network_error` and RETAINS the part -- the resumable case,
    covered by the connection-drop test.)"""
    server = stub_server
    content = server.state.files.setdefault("/model.gguf", _gguf_body(b"sizegate"))
    with pytest.raises(ModelRuntimeError) as excinfo:
        download_file(
            _spec(server, size_bytes=len(content) + 1),
            tmp_path / "dest",
            threading.Event(),
            lambda _: None,
        )
    assert excinfo.value.code == "download_size_mismatch"
    assert not (tmp_path / "dest" / "model.gguf").exists()
    assert not (tmp_path / "dest" / "model.gguf.part").exists()


def test_server_500_is_http_error(stub_server: Any, tmp_path: Path) -> None:
    server = stub_server
    server.state.status["/model.gguf"] = 500
    with pytest.raises(ModelRuntimeError) as excinfo:
        download_file(
            _spec(server, sha256=None), tmp_path / "dest", threading.Event(), lambda _: None
        )
    assert excinfo.value.code == "download_http_error"


def test_connection_drop_is_network_error_and_part_survives(
    stub_server: Any, tmp_path: Path
) -> None:
    server = stub_server
    content = server.state.files.setdefault("/model.gguf", _gguf_body(b"dropped"))
    server.state.drop.add("/model.gguf")
    with pytest.raises(ModelRuntimeError) as excinfo:
        download_file(
            _spec(server, sha256=None), tmp_path / "dest", threading.Event(), lambda _: None
        )
    assert excinfo.value.code == "download_network_error"
    # A network failure retains the part: the next attempt resumes.
    part = tmp_path / "dest" / "model.gguf.part"
    assert part.exists()
    # And the resume actually completes against the healthy server.
    server.state.drop.clear()
    final_path, digest = download_file(
        _spec(server), tmp_path / "dest", threading.Event(), lambda _: None
    )
    assert final_path.read_bytes() == content
    assert digest == hashlib.sha256(content).hexdigest()


def test_http_redirect_target_is_refused(stub_server: Any, tmp_path: Path) -> None:
    server = stub_server
    server.state.files["/elsewhere.gguf"] = _gguf_body(b"elsewhere")
    server.state.redirect["/model.gguf"] = _url(server, "/elsewhere.gguf")
    with pytest.raises(ModelRuntimeError) as excinfo:
        download_file(
            _spec(server, sha256=None), tmp_path / "dest", threading.Event(), lambda _: None
        )
    assert excinfo.value.code == "download_url_unsupported"
    assert not (tmp_path / "dest" / "model.gguf.part").exists()


def test_redirect_cap_is_five_hops(stub_server: Any, tmp_path: Path) -> None:
    server = stub_server
    server.state.files["/model.gguf"] = _gguf_body(b"loop")
    # A self-redirect loop: hop 6 must fail closed, not spin.
    server.state.redirect["/model.gguf"] = _url(server, "/model.gguf")
    with pytest.raises(ModelRuntimeError) as excinfo:
        download_file(
            _spec(server, sha256=None), tmp_path / "dest", threading.Event(), lambda _: None
        )
    assert excinfo.value.code == "download_url_unsupported"


def test_disk_space_pre_flight_refuses_before_any_request(
    stub_server: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil as shutil_module

    server = stub_server
    server.state.files["/model.gguf"] = _gguf_body(b"space")

    class _FullUsage:
        free = 0

    def _full_disk(_path: object) -> Any:
        return _FullUsage()

    monkeypatch.setattr(shutil_module, "disk_usage", _full_disk)
    with pytest.raises(ModelRuntimeError) as excinfo:
        download_file(_spec(server), tmp_path / "dest", threading.Event(), lambda _: None)
    assert excinfo.value.code == "disk_space_insufficient"
    # Nothing was written and the server was never contacted.
    assert not (tmp_path / "dest" / "model.gguf.part").exists()
    assert server.state.request_count == {}


# --- URL rules ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://huggingface.co/repo/resolve/x/file.gguf", True),
        ("http://127.0.0.1:8/path.gguf", True),
        ("http://[::1]:8/path.gguf", True),
        ("http://localhost:8/path.gguf", True),
        ("http://example.com/path.gguf", False),
        ("http://10.0.0.1/path.gguf", False),
        ("ftp://127.0.0.1/path.gguf", False),
        ("file:///etc/hostname", False),
        ("https:///no-host.gguf", False),
    ],
)
def test_validate_user_url_matrix(url: str, allowed: bool) -> None:
    if allowed:
        validate_user_url(url)  # must not raise
    else:
        with pytest.raises(ModelRuntimeError) as excinfo:
            validate_user_url(url)
        assert excinfo.value.code == "download_url_unsupported"


# --- DownloadManager: states, registration, concurrency -----------------------------


class _ManagerEnv:
    def __init__(self, tmp_path: Path) -> None:
        self.database = initialize_database(tmp_path)
        self.manager = DownloadManager(self.database)
        self.tmp_path = tmp_path


@pytest.fixture()
def manager_env(tmp_path: Path) -> Any:
    env = _ManagerEnv(tmp_path)
    yield env
    env.database.dispose()


def _db_rows(database: Any) -> list[tuple[str, str, int, str]]:
    from sqlalchemy import select

    with database.session() as session:
        return [
            (row.sha256_hash, row.role, row.size_bytes, row.display_name)
            for row in session.scalars(select(ModelFile)).all()
        ]


def test_manager_completes_and_registers_model_file(
    stub_server: Any, manager_env: _ManagerEnv
) -> None:
    server = stub_server
    content = server.state.files.setdefault("/model.gguf", _gguf_body(b"register"))
    download_id = manager_env.manager.start(_request(server, manager_env.tmp_path))
    status = _wait_terminal(manager_env.manager, download_id)
    assert status.state == "completed"
    assert status.error_code is None
    # The registered row IS the verified-import outcome: primary file,
    # its digest, its size, the request's role and display name.
    assert _db_rows(manager_env.database) == [
        (hashlib.sha256(content).hexdigest(), "generation", len(content), "Stub Model")
    ]
    # The final file lives in the destination directory; the part is gone.
    dest = manager_env.tmp_path / "models"
    assert (dest / "model.gguf").read_bytes() == content
    assert not (dest / "model.gguf.part").exists()
    # Completion releases the single slot.
    assert manager_env.manager.active() is None


def test_manager_duplicate_download_refreshes_not_duplicates(
    stub_server: Any, manager_env: _ManagerEnv
) -> None:
    server = stub_server
    server.state.files.setdefault("/model.gguf", _gguf_body(b"duplicate"))
    manager = manager_env.manager
    first = _wait_terminal(manager, manager.start(_request(server, manager_env.tmp_path)))
    assert first.state == "completed"
    second = _wait_terminal(manager, manager.start(_request(server, manager_env.tmp_path)))
    assert second.state == "completed"
    # Same content, same role: one row (refreshed), never two.
    assert len(_db_rows(manager_env.database)) == 1
    with manager_env.database.session() as session:
        from sqlalchemy import select

        row = session.scalars(select(ModelFile)).one()
        assert row.last_verified_at is not None


def test_manager_checksum_mismatch_registers_nothing(
    stub_server: Any, manager_env: _ManagerEnv
) -> None:
    server = stub_server
    server.state.files["/model.gguf"] = _gguf_body(b"actual-content")
    download_id = manager_env.manager.start(
        _request(server, manager_env.tmp_path, sha256="ab" * 32)
    )
    status = _wait_terminal(manager_env.manager, download_id)
    assert status.state == "failed"
    assert status.error_code == "download_checksum_mismatch"
    # Fail closed: no DB row, no final file, no part.
    assert _db_rows(manager_env.database) == []
    dest = manager_env.tmp_path / "models"
    assert not (dest / "model.gguf").exists()
    assert not (dest / "model.gguf.part").exists()
    assert manager_env.manager.active() is None


def test_manager_registration_refuses_non_gguf_content(
    stub_server: Any, manager_env: _ManagerEnv
) -> None:
    server = stub_server
    content = b"definitely not a model file" * 200
    server.state.files["/model.gguf"] = content
    download_id = manager_env.manager.start(_request(server, manager_env.tmp_path))
    status = _wait_terminal(manager_env.manager, download_id)
    # The download itself verified (the declared digest matches); the
    # container sniff at registration is what refuses it.
    assert status.state == "failed"
    assert status.error_code == "invalid_gguf_magic"
    assert _db_rows(manager_env.database) == []


def test_manager_cancel_midstream_retains_part_then_resumes(
    stub_server: Any, manager_env: _ManagerEnv
) -> None:
    """A gated mid-stream cancel: the first 1 MiB hash chunk has been
    written when the cancel lands (the download loop checks the cancel
    event per yielded chunk), the part is retained, and a fresh attempt
    resumes from it to completion. Deterministic -- the stub blocks
    mid-body until the test opens the gate."""
    server = stub_server
    content = server.state.files.setdefault("/slow.gguf", _gguf_body(b"cancel-me", 2 * 1024 * 1024))
    server.state.gate_paths.add("/slow.gguf")
    server.state.gate_prefix = 3 * HASH_CHUNK_BYTES // 2  # 1.5 MiB served, then block
    manager = manager_env.manager
    download_id = manager.start(_request(server, manager_env.tmp_path, path="/slow.gguf"))
    part = manager_env.tmp_path / "models" / "slow.gguf.part"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if part.exists() and part.stat().st_size >= HASH_CHUNK_BYTES:
            break  # the first chunk was yielded, written, and hashed
        time.sleep(0.01)
    else:
        pytest.fail("the first hash chunk never reached the part file")
    status = manager.cancel(download_id)
    assert status.state in {"downloading", "cancelled"}
    server.state.gate.set()  # let the handler finish; the worker must refuse the next chunk
    final = _wait_terminal(manager, download_id)
    assert final.state == "cancelled"
    assert final.error_code is None
    # The part is retained, holding exactly the verified-prefix bytes a
    # later attempt resumes from.
    assert part.exists()
    assert 0 < part.stat().st_size < len(content)
    resumed = _wait_terminal(
        manager, manager.start(_request(server, manager_env.tmp_path, path="/slow.gguf"))
    )
    assert resumed.state == "completed"
    assert (manager_env.tmp_path / "models" / "slow.gguf").read_bytes() == content


def test_manager_cancel_unknown_id_is_not_found(manager_env: _ManagerEnv) -> None:
    with pytest.raises(ModelRuntimeError) as excinfo:
        manager_env.manager.cancel("no-such-download")
    assert excinfo.value.code == "download_not_found"
    with pytest.raises(ModelRuntimeError) as excinfo:
        manager_env.manager.status("no-such-download")
    assert excinfo.value.code == "download_not_found"


def test_second_start_while_active_is_refused(stub_server: Any, manager_env: _ManagerEnv) -> None:
    server = stub_server
    server.state.files.setdefault("/slow.gguf", _gguf_body(b"slot-occupant", 32 * 1024))
    server.state.trickle["/slow.gguf"] = 0.02
    server.state.files.setdefault("/other.gguf", _gguf_body(b"second"))
    manager = manager_env.manager
    first = manager.start(
        _request(server, manager_env.tmp_path, path="/slow.gguf", display_name="First")
    )
    with pytest.raises(ModelRuntimeError) as excinfo:
        manager.start(
            _request(server, manager_env.tmp_path, path="/other.gguf", display_name="Second")
        )
    assert excinfo.value.code == "download_active"
    assert manager.active() == first
    manager.cancel(first)
    _wait_terminal(manager, first)


def test_shutdown_cancels_and_joins(stub_server: Any, manager_env: _ManagerEnv) -> None:
    server = stub_server
    server.state.files["/slow.gguf"] = _gguf_body(b"shutdown", 64 * 1024)
    server.state.trickle["/slow.gguf"] = 0.02
    download_id = manager_env.manager.start(
        _request(server, manager_env.tmp_path, path="/slow.gguf")
    )
    started = time.monotonic()
    manager_env.manager.shutdown()
    elapsed = time.monotonic() - started
    # Bounded: the worker noticed the cancel and the join did not hang.
    assert elapsed < 10.0
    status = manager_env.manager.status(download_id)
    assert status.state == "cancelled"


def test_multi_file_entry_registers_primary_only(
    stub_server: Any, manager_env: _ManagerEnv
) -> None:
    """A two-file (sharded) entry downloads and verifies BOTH files but
    registers the primary (shard 1) -- the file llama-server is given;
    S6-05's model_source holds the full shard list."""
    server = stub_server
    shard1 = server.state.files.setdefault("/shard-00001.gguf", _gguf_body(b"shard-one"))
    shard2 = server.state.files.setdefault("/shard-00002.gguf", _gguf_body(b"shard-two"))
    request = _request(
        server,
        manager_env.tmp_path,
        files=(
            _spec(server, "/shard-00001.gguf"),
            _spec(server, "/shard-00002.gguf"),
        ),
    )
    status = _wait_terminal(manager_env.manager, manager_env.manager.start(request))
    assert status.state == "completed"
    assert [entry_file.filename for entry_file in status.files] == [
        "shard-00001.gguf",
        "shard-00002.gguf",
    ]
    dest = manager_env.tmp_path / "models"
    assert (dest / "shard-00001.gguf").read_bytes() == shard1
    assert (dest / "shard-00002.gguf").read_bytes() == shard2
    assert _db_rows(manager_env.database) == [
        (hashlib.sha256(shard1).hexdigest(), "generation", len(shard1), "Stub Model")
    ]


def test_progress_reports_declared_totals(stub_server: Any, manager_env: _ManagerEnv) -> None:
    server = stub_server
    content = server.state.files.setdefault("/model.gguf", _gguf_body(b"progress"))
    manager = manager_env.manager
    download_id = manager.start(_request(server, manager_env.tmp_path))
    _wait_terminal(manager, download_id)
    status = manager.status(download_id)
    assert len(status.files) == 1
    assert status.files[0].bytes_downloaded == len(content)
    assert status.files[0].bytes_total == len(content)


# --- S6-05: the model_source registry writes -------------------------------------


def _source_rows(database: Any) -> list[dict[str, Any]]:
    import json

    from sqlalchemy import select

    from controlgauge_service.db.models import ModelSource

    with database.session() as session:
        return [
            {
                "origin": row.origin,
                "catalog_model_id": row.catalog_model_id,
                "files": json.loads(row.files_json),
            }
            for row in session.scalars(select(ModelSource)).all()
        ]


def test_registration_writes_full_provenance(stub_server: Any, manager_env: _ManagerEnv) -> None:
    """S6-05: the download's registration writes the 1:1 `model_source`
    provenance in the SAME transaction -- catalog identity plus the
    origin/verification vocabulary on the `model_file` row."""
    server = stub_server
    content = server.state.files.setdefault("/model.gguf", _gguf_body(b"provenance"))
    manager = manager_env.manager
    status = _wait_terminal(
        manager,
        manager.start(_request(server, manager_env.tmp_path, catalog_model_id="stub-entry-q4-k-m")),
    )
    assert status.state == "completed"
    sources = _source_rows(manager_env.database)
    assert len(sources) == 1
    assert sources[0]["origin"] == "catalog_download"
    assert sources[0]["catalog_model_id"] == "stub-entry-q4-k-m"
    assert sources[0]["files"] == [
        {
            "filename": "model.gguf",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }
    ]
    # The model_file row carries the same registry distinction.
    with manager_env.database.session() as session:
        from sqlalchemy import select

        record = session.scalars(select(ModelFile)).one()
        assert record.origin == "catalog_download"
        assert record.verification == "catalog_manifest"


def test_multi_file_registration_covers_every_shard_in_model_source(
    stub_server: Any, manager_env: _ManagerEnv
) -> None:
    """The 7B shard provenance gap closes: both downloaded shards are in
    `model_source.files_json`, while `model_file` still registers only
    the primary (shard 1 -- the file llama-server is given)."""
    server = stub_server
    shard1 = server.state.files.setdefault("/shard-00001.gguf", _gguf_body(b"shard-one"))
    shard2 = server.state.files.setdefault("/shard-00002.gguf", _gguf_body(b"shard-two"))
    request = _request(
        server,
        manager_env.tmp_path,
        files=(
            _spec(server, "/shard-00001.gguf"),
            _spec(server, "/shard-00002.gguf"),
        ),
        catalog_model_id="stub-sharded-entry",
    )
    status = _wait_terminal(manager_env.manager, manager_env.manager.start(request))
    assert status.state == "completed"
    sources = _source_rows(manager_env.database)
    assert len(sources) == 1
    assert sources[0]["files"] == [
        {
            "filename": "shard-00001.gguf",
            "sha256": hashlib.sha256(shard1).hexdigest(),
            "size_bytes": len(shard1),
        },
        {
            "filename": "shard-00002.gguf",
            "sha256": hashlib.sha256(shard2).hexdigest(),
            "size_bytes": len(shard2),
        },
    ]
    assert _db_rows(manager_env.database) == [
        (hashlib.sha256(shard1).hexdigest(), "generation", len(shard1), "Stub Model")
    ]


def test_unknown_size_record_only_download_completes(
    stub_server: Any, manager_env: _ManagerEnv
) -> None:
    """The url_import record-only shape: no declared size (0) and no
    declared digest -- the download completes, the computed digest is
    what gets registered."""
    server = stub_server
    content = server.state.files.setdefault("/url.gguf", _gguf_body(b"record-only"))
    spec = DownloadFileSpec(
        filename="url.gguf", url=_url(server, "/url.gguf"), size_bytes=0, sha256=None
    )
    request = _request(server, manager_env.tmp_path, files=(spec,), display_name="URL Model")
    status = _wait_terminal(manager_env.manager, manager_env.manager.start(request))
    assert status.state == "completed"
    assert status.files[0].bytes_total == 0
    assert status.files[0].bytes_downloaded == len(content)
    assert _db_rows(manager_env.database) == [
        (hashlib.sha256(content).hexdigest(), "generation", len(content), "URL Model")
    ]


def test_chunk_size_is_one_mebibyte() -> None:
    # The streaming contract the memory bound relies on.
    assert HASH_CHUNK_BYTES == 1024 * 1024


def test_nothing_downloads_without_approval(tmp_path: Path) -> None:
    """Approval gating, structural form: constructing the app (and the
    manager) starts nothing -- a download exists only behind the
    authenticated POST."""
    from fastapi.testclient import TestClient

    from controlgauge_service.app import create_app
    from controlgauge_service.config import Settings

    database = initialize_database(tmp_path)
    manager = DownloadManager(database)
    client = TestClient(
        create_app(
            Settings(session_token=VALID_TOKEN, model_dir=tmp_path / "models"),
            database=database,
            downloads=manager,
        )
    )
    try:
        assert manager.active() is None
        listed = client.get("/model-catalog", headers=AUTH)
        assert listed.status_code == 200
        assert manager.active() is None
    finally:
        client.close()
        database.dispose()
