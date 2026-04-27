"""Chimera hub: resident per-user process that owns PySide6 UI + heavy IO.

Thin DCC plugins (``zeno_thin``) discover this hub via a session file under
``$CHIMERA_ROOT/var`` and talk to it over authenticated local HTTP. Keep
imports here light; heavy modules live in submodules.
"""

from chimera_hub.ipc_contract import (
    AUTH_HEADER,
    HEALTH_PATH,
    LOAD_PATH,
    PROTOCOL_VERSION,
    PUBLISH_PATH,
    SHUTDOWN_PATH,
    UI_NAVIGATOR_PATH,
    UI_PALETTE_PATH,
    UI_RAISE_PATH,
    SessionInfo,
    chimera_root,
    session_file,
    var_dir,
)

__all__ = [
    "AUTH_HEADER",
    "HEALTH_PATH",
    "LOAD_PATH",
    "PROTOCOL_VERSION",
    "PUBLISH_PATH",
    "SHUTDOWN_PATH",
    "UI_NAVIGATOR_PATH",
    "UI_PALETTE_PATH",
    "UI_RAISE_PATH",
    "SessionInfo",
    "chimera_root",
    "session_file",
    "var_dir",
]
