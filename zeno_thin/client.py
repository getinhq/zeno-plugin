"""Stdlib HTTP client for the Chimera hub.

Uses ``http.client`` + ``json`` so it imports cleanly inside Blender, Maya,
Houdini, and Nuke's bundled Python without needing ``httpx``/``requests``.

Session discovery: reads ``$CHIMERA_ROOT/var/session.json`` (written by the
hub). ``ensure_hub_running()`` can spawn the hub as a detached subprocess
using a caller-supplied Python interpreter.
"""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from chimera_hub.ipc_contract import (
    AUTH_HEADER,
    CONTENT_TYPE_JSON,
    DEFAULT_BIND_HOST,
    HEALTH_PATH,
    LOAD_PATH,
    PROTOCOL_VERSION,
    PUBLISH_PATH,
    SessionInfo,
    UI_NAVIGATOR_PATH,
    UI_PALETTE_PATH,
    UI_RAISE_PATH,
    chimera_root,
    session_file,
)


class HubNotRunning(RuntimeError):
    """Raised when no hub session is reachable."""


class HubUnauthorised(RuntimeError):
    """Raised on 401 — stale token or wrong session file."""


class HubRequestFailed(RuntimeError):
    """Raised for other non-2xx responses; ``.status`` carries the HTTP code."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


def read_session() -> SessionInfo | None:
    """Return the current ``SessionInfo`` or ``None`` if the file is missing/stale."""
    path = session_file()
    if not path.is_file():
        return None
    try:
        return SessionInfo.from_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def discover_session(*, timeout_s: float = 0.5) -> SessionInfo | None:
    """Return a ``SessionInfo`` that is reachable right now, else ``None``."""
    info = read_session()
    if info is None:
        return None
    try:
        conn = http.client.HTTPConnection(info.host, info.port, timeout=timeout_s)
        conn.request("GET", HEALTH_PATH, headers={AUTH_HEADER: info.token})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        if resp.status == 200:
            return info
    except OSError:
        return None
    return None


def ensure_hub_running(
    *,
    python_exe: str | None = None,
    timeout_s: float = 10.0,
    extra_args: list[str] | None = None,
) -> SessionInfo:
    """Return a live ``SessionInfo`` — spawning the hub if needed.

    ``python_exe`` should point at the interpreter that has the hub venv
    available. Falls back to ``sys.executable``; callers supply the correct
    path when running inside a DCC whose Python can't import ``chimera_hub``.
    """
    existing = discover_session()
    if existing is not None:
        return existing

    py = python_exe or sys.executable
    cmd = [py, "-m", "chimera_hub"]
    if extra_args:
        cmd.extend(extra_args)

    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:  # pragma: no cover - Windows
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(cmd, **kwargs)

    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        info = discover_session()
        if info is not None:
            return info
        time.sleep(0.1)
    raise HubNotRunning(
        f"Chimera hub did not start within {timeout_s}s "
        f"(CHIMERA_ROOT={chimera_root()})"
        + (f" last={last_err}" if last_err else "")
    )


class ThinHubClient:
    """Minimal JSON HTTP client; one instance per session is fine."""

    def __init__(self, session: SessionInfo) -> None:
        self._session = session

    @property
    def session(self) -> SessionInfo:
        return self._session

    # -- public shortcuts -----------------------------------------------------
    def health(self) -> dict[str, Any]:
        return self._request("GET", HEALTH_PATH)

    def publish(
        self,
        *,
        path: str | Path,
        project: str,
        asset: str,
        representation: str = "blend",
        version: str = "next",
        dcc: str = "",
        pipeline_stage: str = "",
        task_id: str = "",
        extras: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged_extras: dict[str, Any] = dict(extras or {})
        stage = (pipeline_stage or "").strip().lower()
        if stage and "pipeline_stage" not in merged_extras:
            merged_extras["pipeline_stage"] = stage
        tid = (task_id or "").strip()
        if tid and "task_id" not in merged_extras:
            merged_extras["task_id"] = tid
        body = {
            "path": str(path),
            "project": project,
            "asset": asset,
            "representation": representation,
            "version": version,
            "dcc": dcc,
            "extras": merged_extras,
        }
        return self._request("POST", PUBLISH_PATH, body=body)

    def load(
        self,
        *,
        project: str,
        asset: str,
        version: str = "latest",
        representation: str = "blend",
    ) -> dict[str, Any]:
        body = {
            "project": project,
            "asset": asset,
            "version": version,
            "representation": representation,
        }
        return self._request("POST", LOAD_PATH, body=body)

    def open_palette(
        self,
        *,
        prefs_default_project: str = "",
        launch_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            UI_PALETTE_PATH,
            body={
                "prefs_default_project": prefs_default_project,
                "launch_hint": launch_hint or {},
            },
        )

    def open_navigator(self) -> dict[str, Any]:
        return self._request("POST", UI_NAVIGATOR_PATH, body={})

    def raise_ui(self) -> dict[str, Any]:
        return self._request("POST", UI_RAISE_PATH, body={})

    # -- transport ------------------------------------------------------------
    def _request(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
        info = self._session
        headers = {AUTH_HEADER: info.token}
        data: bytes = b""
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = CONTENT_TYPE_JSON
            headers["Content-Length"] = str(len(data))

        conn = http.client.HTTPConnection(info.host, info.port, timeout=30.0)
        try:
            conn.request(method, path, body=data if data else None, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status == 401:
                raise HubUnauthorised("invalid or missing token — hub may have restarted")
            if resp.status >= 400:
                try:
                    payload = json.loads(raw.decode("utf-8"))
                    message = str(payload.get("message") or raw.decode("utf-8", errors="replace"))
                except Exception:
                    message = raw.decode("utf-8", errors="replace")
                raise HubRequestFailed(resp.status, message)
            if not raw:
                return {}
            try:
                return json.loads(raw.decode("utf-8"))
            except ValueError as exc:
                raise HubRequestFailed(resp.status, f"invalid JSON response: {exc}") from exc
        except (ConnectionRefusedError, OSError) as exc:
            raise HubNotRunning(f"hub unreachable at {info.host}:{info.port}: {exc}") from exc
        finally:
            try:
                conn.close()
            except Exception:
                pass


__all__ = [
    "DEFAULT_BIND_HOST",
    "HubNotRunning",
    "HubRequestFailed",
    "HubUnauthorised",
    "PROTOCOL_VERSION",
    "ThinHubClient",
    "discover_session",
    "ensure_hub_running",
    "read_session",
]
