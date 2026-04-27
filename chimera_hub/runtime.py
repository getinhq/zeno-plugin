"""Hub runtime: token generation, session file management, single-instance lock."""
from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from chimera_hub.ipc_contract import (
    DEFAULT_BIND_HOST,
    SessionInfo,
    chimera_root,
    log_dir,
    session_file,
    var_dir,
)


LOCK_FILENAME = "session.lock"
PID_FILENAME = "session.pid"


def _atomic_write(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    try:
        os.chmod(tmp, mode)
    except OSError:  # pragma: no cover - Windows ACLs
        pass
    os.replace(tmp, path)


def generate_token() -> str:
    """Return a fresh 256-bit URL-safe token."""
    return secrets.token_urlsafe(32)


def pick_ephemeral_port(host: str = DEFAULT_BIND_HOST) -> int:
    """Bind, read port, close — lets the OS pick an unused port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def existing_session() -> SessionInfo | None:
    """Return the current session if a hub is already running; else None."""
    path = session_file()
    if not path.is_file():
        return None
    try:
        info = SessionInfo.from_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not is_pid_alive(info.pid):
        return None
    try:
        with socket.create_connection((info.host, info.port), timeout=0.25):
            return info
    except OSError:
        return None


def write_session(info: SessionInfo) -> None:
    _atomic_write(session_file(), info.to_json(), mode=0o600)


def clear_session() -> None:
    for name in (session_file().name, PID_FILENAME):
        p = var_dir() / name
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:  # pragma: no cover - permissions
            pass


def acquire_single_instance_or_die() -> None:
    """Refuse to start a second hub under the same ``CHIMERA_ROOT``."""
    existing = existing_session()
    if existing is not None:
        raise SystemExit(
            f"Chimera hub already running (pid={existing.pid}, "
            f"port={existing.port}). Refusing to start a second instance."
        )


def configure_logging() -> logging.Logger:
    """Rotating file log under ``$CHIMERA_ROOT/var/logs/hub.log`` + stderr."""
    root = logging.getLogger("chimera_hub")
    if root.handlers:
        return root
    root.setLevel(logging.INFO)

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(stderr)

    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        file_h = RotatingFileHandler(
            log_dir() / "hub.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_h.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(file_h)
    except OSError:  # pragma: no cover
        pass

    return root


def prepare_session(*, host: str | None = None, port: int | None = None) -> SessionInfo:
    """Allocate token + port and write ``session.json``.

    Returns the ``SessionInfo`` so the server can bind the same port we reserved.
    """
    h = host or DEFAULT_BIND_HOST
    p = port or pick_ephemeral_port(h)
    info = SessionInfo(host=h, port=p, token=generate_token(), pid=os.getpid())
    write_session(info)
    _write_pid_file()
    return info


def _write_pid_file() -> None:
    _atomic_write(var_dir() / PID_FILENAME, str(os.getpid()), mode=0o600)


def wait_for_health(info: SessionInfo, *, timeout_s: float = 5.0) -> bool:
    """Poll the hub HTTP until ``/v1/health`` returns ok or timeout elapses."""
    import http.client

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            conn = http.client.HTTPConnection(info.host, info.port, timeout=0.5)
            conn.request("GET", "/v1/health", headers={"X-Chimera-Token": info.token})
            resp = conn.getresponse()
            if resp.status == 200:
                return True
        except OSError:
            pass
        finally:
            try:
                conn.close()  # type: ignore[unboundlocal]
            except Exception:
                pass
        time.sleep(0.05)
    return False


def describe_root() -> dict[str, str]:
    """Diagnostics: show where session/log/config live."""
    return {
        "chimera_root": str(chimera_root()),
        "session_file": str(session_file()),
        "log_dir": str(log_dir()),
    }


__all__ = [
    "acquire_single_instance_or_die",
    "clear_session",
    "configure_logging",
    "describe_root",
    "existing_session",
    "generate_token",
    "is_pid_alive",
    "pick_ephemeral_port",
    "prepare_session",
    "wait_for_health",
    "write_session",
]
