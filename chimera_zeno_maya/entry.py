"""Maya host integration for ``zeno_ui`` dialogs.

PySide / shiboken:
    Maya ships a Qt build and matching ``shiboken`` (PySide2 + shiboken2 on older
    releases; PySide6 + shiboken6 on newer ones). This module uses the same
    binding as ``zeno_ui.qt_compat`` (PySide6 with fallback to PySide2) and
    imports ``shiboken6`` or ``shiboken2`` accordingly so ``wrapInstance`` matches
    Maya's Qt DLLs. If you see crashes or blank parents, ensure you are not mixing
    a pip-installed PySide with Maya's built-in Qt.

Usage:
    Add the ``zeno-plugin`` install directory to ``MAYA_SCRIPT_PATH`` and
    ``PYTHONPATH``, then in Maya::

        import chimera_zeno_maya
        chimera_zeno_maya.open_navigator()
        chimera_zeno_maya.open_command_palette()
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from zeno_client import CacheConfig, LocalCache, ZenoClient
from zeno_client.publisher import publish_chunked_file
from zeno_client.launch_context import read_launch_context_from_environ

from zeno_ui.qt_dialogs import ZenoNavigatorDialog, ZenoPaletteDialog


def _api_base_url() -> str | None:
    u = os.environ.get("ZENO_API_BASE_URL", "").strip()
    return u or None


def _make_client() -> ZenoClient:
    return ZenoClient(base_url=_api_base_url())


def _make_cache() -> LocalCache:
    max_gb = int(os.environ.get("ZENO_CACHE_MAX_GB", "50") or 50)
    return LocalCache(CacheConfig(max_bytes=max_gb * 1024 * 1024 * 1024))


def _maya_main_window() -> Any | None:
    from maya import OpenMayaUI as omui

    from zeno_ui.qt_compat import get_qt_modules

    try:
        from shiboken6 import wrapInstance  # type: ignore
    except ImportError:  # pragma: no cover - Maya version specific
        from shiboken2 import wrapInstance  # type: ignore

    QtWidgets, _, _ = get_qt_modules()
    ptr = omui.MQtUtil.mainWindow()
    if ptr is None:
        return None
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def _launch_hint() -> dict[str, Any] | None:
    ctx = read_launch_context_from_environ()
    if ctx is None:
        return None
    return {
        "project_id": ctx.project_id,
        "project_code": ctx.project_code or "",
        "asset_id": ctx.asset_id or "",
    }


def _load_asset_maya(
    *,
    project: str,
    asset: str,
    version: str,
    representation: str,
) -> None:
    import maya.cmds as cmds

    from zeno_client.palette_catalog import build_asset_uri

    client = _make_client()
    cache = _make_cache()
    uri = build_asset_uri(project, asset, version, representation)
    local_path = cache.ensure_uri_cached(uri, client=client)
    cmds.file(str(local_path), open=True, force=True)


def _publish_current_maya(*, project: str, asset: str, representation: str) -> None:
    import maya.cmds as cmds

    scene = cmds.file(q=True, sceneName=True)
    if not scene:
        raise RuntimeError("Save the Maya scene before publishing.")
    path = Path(scene)
    client = _make_client()
    publish_chunked_file(
        client=client,
        project=project,
        asset=asset,
        representation=representation,
        path=path,
        version="next",
        filename=path.name,
        dcc="maya",
    )


def open_navigator() -> None:
    """Show themed Navigator dialog (modal)."""
    parent = _maya_main_window()
    dlg = ZenoNavigatorDialog(
        client=_make_client(),
        parent=parent,
        default_version="latest",
        default_representation="ma",
        on_open=lambda **kw: _load_asset_maya(**kw),
    )
    dlg.exec_modal()


def open_command_palette() -> None:
    """Show themed Command Palette dialog (modal)."""
    parent = _maya_main_window()
    prefs = os.environ.get("ZENO_DEFAULT_PROJECT", "").strip()
    dlg = ZenoPaletteDialog(
        client=_make_client(),
        parent=parent,
        prefs_default_project=prefs,
        launch_hint=_launch_hint(),
        default_representation="ma",
        on_load=lambda **kw: _load_asset_maya(**kw),
        on_publish=lambda **kw: _publish_current_maya(**kw),
    )
    dlg.exec_modal()


__all__ = ["open_command_palette", "open_navigator"]
