"""IPC contract shared by Chimera hub and thin DCC clients.

This module intentionally uses **stdlib-only imports** so ``zeno_thin`` (which
must stay zero-dependency) can import the same constants and dataclasses as
the hub. Keep heavy imports (``httpx``, ``blake3``, ``PySide6``) out of here.

Transport: HTTP/1.1 over ``127.0.0.1:<port>``.
Auth:      every request must carry header ``X-Chimera-Token`` whose value
           matches the token in ``session.json``.
Discovery: hub writes ``$CHIMERA_ROOT/var/session.json`` with the ephemeral
           port + token. Clients read it to locate the hub.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
AUTH_HEADER = "X-Chimera-Token"
CONTENT_TYPE_JSON = "application/json"
DEFAULT_BIND_HOST = "127.0.0.1"

HEALTH_PATH = "/v1/health"
PUBLISH_PATH = "/v1/publish"
LOAD_PATH = "/v1/load"
UI_PALETTE_PATH = "/v1/ui/palette"
UI_NAVIGATOR_PATH = "/v1/ui/navigator"
UI_RAISE_PATH = "/v1/ui/raise"
SHUTDOWN_PATH = "/v1/shutdown"


def chimera_root() -> Path:
    """Resolve ``$CHIMERA_ROOT`` (env override) or a per-user default."""
    env = os.environ.get("CHIMERA_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    home = Path(os.path.expanduser("~"))
    return home / ".chimera"


def var_dir() -> Path:
    return chimera_root() / "var"


def session_file() -> Path:
    return var_dir() / "session.json"


def log_dir() -> Path:
    return chimera_root() / "var" / "logs"


@dataclass
class SessionInfo:
    """Written by the hub on start, read by clients for discovery."""

    host: str
    port: int
    token: str
    pid: int
    protocol_version: int = PROTOCOL_VERSION

    def url(self, path: str) -> str:
        base = f"http://{self.host}:{self.port}"
        return base + (path if path.startswith("/") else "/" + path)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, data: str) -> "SessionInfo":
        raw = json.loads(data)
        return cls(
            host=str(raw.get("host") or DEFAULT_BIND_HOST),
            port=int(raw["port"]),
            token=str(raw["token"]),
            pid=int(raw.get("pid") or 0),
            protocol_version=int(raw.get("protocol_version") or PROTOCOL_VERSION),
        )


# --- Request / response schemas ------------------------------------------------
#
# These are plain dataclasses used for **documentation + type-hinting**. On the
# wire they are simple JSON objects so the thin client (no pydantic / dataclass
# imports wanted at every call site) can construct dict literals.


@dataclass
class PublishRequest:
    path: str
    project: str
    asset: str
    representation: str = "blend"
    version: str = "next"
    dcc: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishResponse:
    ok: bool
    version: int | None = None
    message: str = ""


@dataclass
class LoadRequest:
    project: str
    asset: str
    version: str = "latest"
    representation: str = "blend"


@dataclass
class LoadResponse:
    ok: bool
    local_path: str = ""
    message: str = ""


@dataclass
class UiOpenRequest:
    prefs_default_project: str = ""
    launch_hint: dict[str, Any] | None = None


@dataclass
class UiOpenResponse:
    ok: bool
    raised: bool = False
    message: str = ""


@dataclass
class HealthResponse:
    ok: bool
    version: int = PROTOCOL_VERSION
    pid: int = 0
    chimera_root: str = ""


def env_hub_root() -> Path:
    """Alias for ``chimera_root`` to make intent obvious at call sites."""
    return chimera_root()


__all__ = [
    "AUTH_HEADER",
    "CONTENT_TYPE_JSON",
    "DEFAULT_BIND_HOST",
    "HEALTH_PATH",
    "LOAD_PATH",
    "PROTOCOL_VERSION",
    "PUBLISH_PATH",
    "SHUTDOWN_PATH",
    "UI_NAVIGATOR_PATH",
    "UI_PALETTE_PATH",
    "UI_RAISE_PATH",
    "HealthResponse",
    "LoadRequest",
    "LoadResponse",
    "PublishRequest",
    "PublishResponse",
    "SessionInfo",
    "UiOpenRequest",
    "UiOpenResponse",
    "chimera_root",
    "env_hub_root",
    "log_dir",
    "session_file",
    "var_dir",
]
