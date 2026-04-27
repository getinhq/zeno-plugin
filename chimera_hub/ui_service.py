"""Hub-side UI service: Qt dialogs live on the hub's main thread.

HTTP requests arrive on worker threads; they enqueue UI work via
``QMetaObject.invokeMethod(..., Qt.QueuedConnection)`` so creation and raising
of widgets always happens on the thread that owns the QApplication.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from chimera_hub import handlers
from chimera_hub.ipc_contract import LoadRequest, PublishRequest

_log = logging.getLogger("chimera_hub.ui_service")


class HubUiService:
    """Creates and raises Zeno dialogs inside the hub QApplication."""

    def __init__(self, client_factory: Any, cache_factory: Any | None = None) -> None:
        self._client_factory = client_factory
        self._cache_factory = cache_factory
        self._palette: Any | None = None
        self._navigator: Any | None = None
        self._lock = threading.Lock()
        # Latest hint recorded when a DCC opens the palette. Tells us which
        # file is currently open so Publish knows what to upload.
        self._latest_launch_hint: dict[str, Any] = {}

    # -- public API (called from HTTP worker threads) -------------------------
    def open_palette(self, *, prefs_default_project: str = "", launch_hint: dict | None = None) -> bool:
        # Cache the hint so button callbacks can read `current_file`/`dcc`
        # even after `open_palette` has returned.
        self._latest_launch_hint = dict(launch_hint or {})
        self._invoke_on_gui(lambda: self._show_palette(prefs_default_project, launch_hint))
        return True

    def open_navigator(self) -> bool:
        self._invoke_on_gui(self._show_navigator)
        return True

    def raise_visible_windows(self) -> bool:
        def _do() -> None:
            for dlg in (self._palette, self._navigator):
                if dlg is None:
                    continue
                try:
                    dlg.widget().raise_()
                    dlg.widget().activateWindow()
                except Exception:
                    pass

        self._invoke_on_gui(_do)
        return True

    # -- Qt-side (main thread) ------------------------------------------------
    def _show_palette(self, prefs_default_project: str, launch_hint: dict | None) -> None:
        from zeno_ui.qt_dialogs import ZenoPaletteDialog

        with self._lock:
            if self._palette is not None:
                try:
                    self._palette.widget().raise_()
                    self._palette.widget().activateWindow()
                    self._palette.widget().show()
                    return
                except Exception:
                    self._palette = None

            service = self

            def on_load(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN003
                _log.info("palette load: %s", kwargs)
                return service._dispatch_load(**kwargs)

            def on_publish(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN003
                _log.info("palette publish: %s", kwargs)
                return service._dispatch_publish(**kwargs)

            dlg = ZenoPaletteDialog(
                client=self._client_factory(),
                parent=None,
                prefs_default_project=prefs_default_project,
                launch_hint=launch_hint,
                default_representation="blend",
                on_load=on_load,
                on_publish=on_publish,
                stay_on_top=False,
            )
            self._palette = dlg

        dlg.show_non_modal()

    def _show_navigator(self) -> None:
        from zeno_ui.qt_dialogs import ZenoNavigatorDialog

        with self._lock:
            if self._navigator is not None:
                try:
                    self._navigator.widget().raise_()
                    self._navigator.widget().activateWindow()
                    self._navigator.widget().show()
                    return
                except Exception:
                    self._navigator = None

            def on_open(**kwargs: Any) -> None:  # noqa: ANN003
                _log.info("navigator open: %s", kwargs)

            dlg = ZenoNavigatorDialog(
                client=self._client_factory(),
                parent=None,
                default_version="latest",
                default_representation="blend",
                on_open=on_open,
                stay_on_top=False,
            )
            self._navigator = dlg

        dlg.show_non_modal()

    def _invoke_on_gui(self, fn: Any) -> None:
        """Queue ``fn`` to run on the Qt main thread via a single-shot timer."""
        from zeno_ui.qt_compat import ensure_qapplication, get_qt_modules

        ensure_qapplication()
        _, QtCore, _ = get_qt_modules()
        QtCore.QTimer.singleShot(0, fn)

    # -- palette action dispatch ---------------------------------------------
    def _dispatch_publish(
        self,
        *,
        project: str = "",
        asset: str = "",
        representation: str = "",
        version: str = "next",
        pipeline_stage: str = "",
        task_id: str = "",
    ) -> dict[str, Any]:
        """Turn a palette Publish click into an actual ``handlers.publish`` call.

        The hub does not know which file the artist has open — that state
        lives in the DCC process. Blender/Maya forward it via ``launch_hint``
        when opening the palette, and we cache the last-seen value here.
        """
        hint = self._latest_launch_hint or {}
        path = str(hint.get("current_file") or "").strip()
        if not path:
            return {
                "ok": False,
                "message": (
                    "Hub does not know which file to publish. Re-open the "
                    "palette from the DCC after saving the current scene."
                ),
            }
        dcc = str(hint.get("dcc") or "").strip()
        stage = (pipeline_stage or "").strip().lower()
        tid = (task_id or "").strip()
        extras: dict[str, Any] = {}
        if stage:
            extras["pipeline_stage"] = stage
        if tid:
            extras["task_id"] = tid
        req = PublishRequest(
            path=path,
            project=project,
            asset=asset,
            representation=representation,
            version=version or "next",
            dcc=dcc,
            extras=extras,
        )
        try:
            return handlers.publish(req, client_factory=self._client_factory)
        except Exception as exc:  # noqa: BLE001
            _log.exception("hub palette publish dispatch failed")
            return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}

    def _dispatch_load(
        self,
        *,
        project: str = "",
        asset: str = "",
        version: str = "latest",
        representation: str = "",
    ) -> dict[str, Any]:
        """Resolve and cache an asset version; DCC follows up on its own."""
        if self._cache_factory is None:
            # No cache factory wired — fall back to a log-only response so the
            # palette still displays a clear message rather than silently
            # closing.
            return {
                "ok": False,
                "message": "Hub load is unavailable (no cache factory configured).",
            }
        req = LoadRequest(
            project=project,
            asset=asset,
            version=version or "latest",
            representation=representation,
        )
        try:
            return handlers.load(
                req,
                client_factory=self._client_factory,
                cache_factory=self._cache_factory,
            )
        except Exception as exc:  # noqa: BLE001
            _log.exception("hub palette load dispatch failed")
            return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}


__all__ = ["HubUiService"]
