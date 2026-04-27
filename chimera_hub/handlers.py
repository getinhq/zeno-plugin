"""Hub request handlers: publish, load, health, UI open.

Pure Python business logic decoupled from the HTTP server so it can be unit
tested without spinning up sockets. UI handlers defer to ``ui_service`` which
marshals dialog creation onto the Qt main thread.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from chimera_hub.ipc_contract import (
    HealthResponse,
    LoadRequest,
    LoadResponse,
    PROTOCOL_VERSION,
    PublishRequest,
    PublishResponse,
    UiOpenRequest,
    UiOpenResponse,
    chimera_root,
)

_log = logging.getLogger("chimera_hub.handlers")


def health() -> dict[str, Any]:
    return asdict(
        HealthResponse(
            ok=True,
            version=PROTOCOL_VERSION,
            pid=os.getpid(),
            chimera_root=str(chimera_root()),
        )
    )


def publish(req: PublishRequest, *, client_factory: Callable[[], Any]) -> dict[str, Any]:
    """Execute a chunked publish inside the hub."""
    from zeno_client.publisher import publish_chunked_file

    path = Path(req.path).expanduser()
    if not path.is_file():
        return asdict(
            PublishResponse(ok=False, message=f"Path not found: {path}")
        )
    extras = req.extras or {}
    stage = str(extras.get("pipeline_stage") or "").strip().lower()
    task_id = str(extras.get("task_id") or "").strip() or None
    try:
        client = client_factory()
        result = publish_chunked_file(
            client=client,
            project=req.project,
            asset=req.asset,
            representation=req.representation or path.suffix.lstrip(".") or "blend",
            path=path,
            version=req.version or "next",
            filename=path.name,
            dcc=req.dcc or None,
            pipeline_stage=stage,
            task_id=task_id,
        )
    except Exception as exc:  # noqa: BLE001
        _log.exception("publish failed")
        return asdict(PublishResponse(ok=False, message=f"{type(exc).__name__}: {exc}"))

    registered = getattr(result, "registered_version", None) or {}
    version_num = None
    try:
        version_num = int(registered.get("version_number"))
    except Exception:
        version_num = None
    return asdict(PublishResponse(ok=True, version=version_num, message="published"))


def load(req: LoadRequest, *, client_factory: Callable[[], Any], cache_factory: Callable[[], Any]) -> dict[str, Any]:
    """Ensure the requested asset version is cached locally; return local path."""
    from zeno_client.palette_catalog import build_asset_uri

    try:
        client = client_factory()
        cache = cache_factory()
        uri = build_asset_uri(req.project, req.asset, req.version or "latest", req.representation or "blend")
        local_path = cache.ensure_uri_cached(uri, client=client)
    except Exception as exc:  # noqa: BLE001
        _log.exception("load failed")
        return asdict(LoadResponse(ok=False, message=f"{type(exc).__name__}: {exc}"))
    return asdict(LoadResponse(ok=True, local_path=str(local_path)))


def ui_open_palette(req: UiOpenRequest, *, ui_service: Any) -> dict[str, Any]:
    try:
        raised = ui_service.open_palette(
            prefs_default_project=req.prefs_default_project,
            launch_hint=req.launch_hint,
        )
    except Exception as exc:  # noqa: BLE001
        _log.exception("ui_open_palette failed")
        return asdict(UiOpenResponse(ok=False, message=f"{type(exc).__name__}: {exc}"))
    return asdict(UiOpenResponse(ok=True, raised=bool(raised)))


def ui_open_navigator(req: UiOpenRequest, *, ui_service: Any) -> dict[str, Any]:
    try:
        raised = ui_service.open_navigator()
    except Exception as exc:  # noqa: BLE001
        _log.exception("ui_open_navigator failed")
        return asdict(UiOpenResponse(ok=False, message=f"{type(exc).__name__}: {exc}"))
    return asdict(UiOpenResponse(ok=True, raised=bool(raised)))


def ui_raise(*, ui_service: Any) -> dict[str, Any]:
    try:
        raised = ui_service.raise_visible_windows()
    except Exception as exc:  # noqa: BLE001
        _log.exception("ui_raise failed")
        return asdict(UiOpenResponse(ok=False, message=f"{type(exc).__name__}: {exc}"))
    return asdict(UiOpenResponse(ok=True, raised=bool(raised)))


__all__ = [
    "health",
    "load",
    "publish",
    "ui_open_navigator",
    "ui_open_palette",
    "ui_raise",
]
