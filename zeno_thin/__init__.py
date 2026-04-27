"""Stdlib-only thin client for talking to the Chimera hub.

Designed to be dropped into DCC addons (Blender/Maya/Houdini/Nuke) without
any pip-installed dependencies. Imports only Python stdlib; re-exports the
IPC contract constants so callers have a single import surface.
"""

from zeno_thin.client import (
    HubNotRunning,
    HubRequestFailed,
    HubUnauthorised,
    ThinHubClient,
    discover_session,
    ensure_hub_running,
    read_session,
)

__all__ = [
    "HubNotRunning",
    "HubRequestFailed",
    "HubUnauthorised",
    "ThinHubClient",
    "discover_session",
    "ensure_hub_running",
    "read_session",
]
