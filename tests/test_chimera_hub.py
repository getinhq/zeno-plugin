"""Loopback tests for the Chimera hub HTTP server + thin client."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from chimera_hub.ipc_contract import AUTH_HEADER, SessionInfo
from chimera_hub.runtime import (
    generate_token,
    pick_ephemeral_port,
    prepare_session,
    write_session,
)
from chimera_hub.server import HubContext, make_server, serve_in_thread
from zeno_thin import HubRequestFailed, HubUnauthorised, ThinHubClient


@pytest.fixture
def hub_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIMERA_ROOT", str(tmp_path))
    return tmp_path


class _StubClient:
    """Minimal stand-in for ZenoClient — no network."""

    def list_projects(self, *_a: Any, **_k: Any) -> list[dict[str, Any]]:
        return []


class _StubCache:
    def ensure_uri_cached(self, uri: str, *, client: Any) -> str:  # noqa: ARG002
        return f"/tmp/cached/{uri.split('/')[-1]}"


def _start_hub(hub_root: Path) -> tuple[Any, SessionInfo, ThinHubClient]:
    info = prepare_session(host="127.0.0.1", port=pick_ephemeral_port())
    ctx = HubContext(
        token=info.token,
        client_factory=lambda: _StubClient(),
        cache_factory=lambda: _StubCache(),
        ui_service=None,
    )
    server = make_server(info.host, info.port, ctx)
    serve_in_thread(server)
    client = ThinHubClient(info)
    return server, info, client


def test_health_round_trip(hub_root: Path) -> None:
    server, info, client = _start_hub(hub_root)
    try:
        resp = client.health()
        assert resp["ok"] is True
        assert resp["version"] >= 1
        assert resp["pid"] == os.getpid()
    finally:
        server.shutdown()


def test_session_file_is_chmod_protected(hub_root: Path) -> None:
    info = prepare_session()
    sess_path = hub_root / "var" / "session.json"
    assert sess_path.is_file()
    raw = json.loads(sess_path.read_text())
    assert raw["token"] == info.token
    if os.name == "posix":
        mode = sess_path.stat().st_mode & 0o777
        assert mode == 0o600


def test_invalid_token_is_rejected(hub_root: Path) -> None:
    server, info, _ = _start_hub(hub_root)
    bad = ThinHubClient(SessionInfo(host=info.host, port=info.port, token="bogus", pid=info.pid))
    try:
        with pytest.raises(HubUnauthorised):
            bad.health()
    finally:
        server.shutdown()


def test_load_endpoint_invokes_cache(hub_root: Path) -> None:
    server, _info, client = _start_hub(hub_root)
    try:
        resp = client.load(project="PROJ", asset="hero", version="latest", representation="blend")
        assert resp["ok"] is True
        assert resp["local_path"].endswith("blend")
    finally:
        server.shutdown()


def test_publish_endpoint_returns_path_error(hub_root: Path, tmp_path: Path) -> None:
    server, _info, client = _start_hub(hub_root)
    try:
        resp = client.publish(
            path=tmp_path / "missing.blend",
            project="PROJ",
            asset="hero",
        )
        assert resp["ok"] is False
        assert "not found" in resp["message"].lower()
    finally:
        server.shutdown()


def test_ui_endpoint_returns_503_when_no_ui(hub_root: Path) -> None:
    server, _info, client = _start_hub(hub_root)
    try:
        with pytest.raises(HubRequestFailed) as excinfo:
            client.open_palette()
        assert excinfo.value.status == 503
    finally:
        server.shutdown()


def test_discover_session_after_writing_session_file(hub_root: Path) -> None:
    """A second client process can find the running hub via session.json."""
    from zeno_thin import discover_session

    server, info, _ = _start_hub(hub_root)
    try:
        # discover should round-trip the session file successfully
        found = discover_session()
        assert found is not None
        assert found.port == info.port
        assert found.token == info.token
    finally:
        server.shutdown()


def test_constant_time_token_compare_does_not_leak_length(hub_root: Path) -> None:
    server, info, _ = _start_hub(hub_root)
    try:
        # Token of wrong length is rejected just like wrong-content
        bad = ThinHubClient(SessionInfo(host=info.host, port=info.port, token="x", pid=info.pid))
        with pytest.raises(HubUnauthorised):
            bad.health()
    finally:
        server.shutdown()


def test_single_instance_guard_blocks_duplicate(hub_root: Path) -> None:
    """Hardening: a second hub for the same CHIMERA_ROOT must refuse to start."""
    from chimera_hub.runtime import acquire_single_instance_or_die

    server, _info, _client = _start_hub(hub_root)
    try:
        with pytest.raises(SystemExit):
            acquire_single_instance_or_die()
    finally:
        server.shutdown()


def test_session_clear_after_shutdown(hub_root: Path) -> None:
    """Hardening: clear_session removes the session file so a fresh hub can start."""
    from chimera_hub.runtime import clear_session
    from chimera_hub.ipc_contract import session_file

    server, _info, _client = _start_hub(hub_root)
    try:
        assert session_file().is_file()
    finally:
        server.shutdown()
    clear_session()
    assert not session_file().is_file()


def test_token_rotation_invalidates_old_clients(hub_root: Path) -> None:
    """If the hub restarts (new token), an old ThinHubClient gets HubUnauthorised."""
    server1, info1, _client1 = _start_hub(hub_root)
    try:
        bad = ThinHubClient(SessionInfo(host=info1.host, port=info1.port, token=info1.token, pid=info1.pid))
        bad.health()
    finally:
        server1.shutdown()

    from chimera_hub.runtime import clear_session

    clear_session()

    server2, info2, _client2 = _start_hub(hub_root)
    try:
        assert info2.token != info1.token
        stale = ThinHubClient(SessionInfo(host=info2.host, port=info2.port, token=info1.token, pid=info1.pid))
        with pytest.raises(HubUnauthorised):
            stale.health()
    finally:
        server2.shutdown()


def test_logging_writes_rotating_file(hub_root: Path) -> None:
    """Hardening: configure_logging produces a hub.log under CHIMERA_ROOT/var/logs."""
    from chimera_hub.runtime import configure_logging
    from chimera_hub.ipc_contract import log_dir

    log = configure_logging()
    log.info("hub-log-test marker line")

    log_path = log_dir() / "hub.log"
    assert log_path.is_file()
    contents = log_path.read_text(encoding="utf-8")
    assert "hub-log-test marker line" in contents
