"""Blender-side Qt runtime: QApplication singleton, event-loop tick, and main-thread queue.

Blender does not drive a Qt event loop. For Qt dialogs to render and stay
interactive, we must:
  1. Create (and keep a strong reference to) a single ``QApplication``.
  2. Call ``QApplication.processEvents()`` on a ``bpy.app.timers`` tick
     **only while a Zeno dialog is actually visible** — otherwise we'd burn
     Blender's main thread forever and make the viewport feel sluggish.
  3. Route any ``bpy.ops.*`` invocations triggered from Qt signals back to
     Blender's main thread via a queue drained by another timer — operators
     are not safe to call from a Qt worker thread.

The original implementation pinned both timers on at 60 Hz / 20 Hz
``persistent=True`` forever after the first palette open, which was the
primary cause of the "Blender feels slow after opening Zeno" regression.
Now the timers auto-stop the moment there is nothing to do, and start
again lazily when the next dialog is shown or the next ``bpy.ops``
callable is enqueued.
"""
from __future__ import annotations

import queue
import threading
from typing import Any, Callable

import bpy

_APP_REF: Any = None
_TICK_HANDLE: Any = None
_DIALOGS: list[Any] = []
_OPS_QUEUE: "queue.Queue[Callable[[], None]]" = queue.Queue()
_OPS_DRAIN_HANDLE: Any = None
# 20 Hz is still comfortably smooth for a form-style palette (buttons,
# combos, text entry) and further reduces the main-thread cost during
# the moments a Zeno dialog is actually open. The previous 30 Hz was
# already a big win over the original 60 Hz, but a tool palette isn't
# a real-time UI, so we can afford to slow the tick a bit more. If a
# future UI needs animation or drag-reorder, raise this for that dialog
# via a dedicated fast-tick helper instead of globally.
_TICK_INTERVAL = 1.0 / 20.0
_DRAIN_INTERVAL = 0.05  # 20 Hz, only while the queue has work
_LOCK = threading.Lock()


def _process_qt_events() -> float | None:
    """Pump Qt events; auto-stop when no Zeno dialogs are visible.

    Returning ``None`` from a ``bpy.app.timers`` callback unregisters it.
    The next time a dialog calls :func:`retain_dialog`, the timer is
    re-registered via :func:`_ensure_tick_timer`.
    """
    global _TICK_HANDLE
    app = _APP_REF
    if app is None:
        _TICK_HANDLE = None
        return None
    try:
        app.processEvents()
    except Exception:
        pass
    if not _DIALOGS:
        _TICK_HANDLE = None
        return None
    return _TICK_INTERVAL


def _drain_ops_queue() -> float | None:
    """Run queued ``bpy.ops.*`` callables; auto-stop when there is nothing left.

    We keep draining while the queue has items even if no dialog is
    currently visible — a publish can legitimately enqueue a final
    ``bpy.ops`` call from the worker after the palette has already
    animated closed.
    """
    global _OPS_DRAIN_HANDLE
    drained = 0
    while drained < 16:
        try:
            fn = _OPS_QUEUE.get_nowait()
        except queue.Empty:
            break
        try:
            fn()
        except Exception as exc:  # pragma: no cover - Blender runtime
            print(f"[chimera] bpy.ops queued callback failed: {exc}")
        drained += 1
    if _OPS_QUEUE.empty():
        _OPS_DRAIN_HANDLE = None
        return None
    return _DRAIN_INTERVAL


def _ensure_tick_timer() -> None:
    """Register the Qt-events tick if it isn't already running.

    ``bpy.app.timers.register`` is safe to call from any thread, but we
    still guard with ``_LOCK`` because two near-simultaneous
    ``retain_dialog`` calls from the main thread would otherwise race on
    ``_TICK_HANDLE``.
    """
    global _TICK_HANDLE
    with _LOCK:
        if _TICK_HANDLE is not None:
            return
        if _APP_REF is None:
            return
        try:
            bpy.app.timers.register(_process_qt_events, persistent=True)
            _TICK_HANDLE = _process_qt_events
        except Exception as exc:  # pragma: no cover - Blender runtime
            print(f"[chimera] could not register Qt tick timer: {exc}")


def _ensure_drain_timer() -> None:
    """Register the ops-drain timer if it isn't already running."""
    global _OPS_DRAIN_HANDLE
    with _LOCK:
        if _OPS_DRAIN_HANDLE is not None:
            return
        try:
            bpy.app.timers.register(_drain_ops_queue, persistent=True)
            _OPS_DRAIN_HANDLE = _drain_ops_queue
        except Exception as exc:  # pragma: no cover - Blender runtime
            print(f"[chimera] could not register ops drain timer: {exc}")


def ensure_qt_runtime() -> tuple[Any, str | None]:
    """Return ``(QApplication, error_message_or_None)``.

    Creates the ``QApplication`` on first call and caches it. Does **not**
    start any ``bpy.app.timers`` — the tick timer is started lazily when
    the first dialog is retained, and the drain timer when the first
    callable is queued. This keeps plain Blender work (viewport, render,
    playback) free of any Qt-related main-thread overhead when no Zeno
    UI is open.
    """
    global _APP_REF

    with _LOCK:
        if _APP_REF is not None:
            return _APP_REF, None

        try:
            from zeno_ui.qt_compat import ensure_qapplication
        except Exception as exc:
            return None, (
                f"Qt binding not available in Blender's Python ({exc}). "
                "Install PySide6 into the Blender interpreter or disable Dashboard UI "
                "in Chimera addon preferences."
            )

        try:
            app = ensure_qapplication()
        except Exception as exc:  # pragma: no cover - env specific
            return None, f"Failed to initialise QApplication: {exc}"

        _APP_REF = app

    return _APP_REF, None


def retain_dialog(dlg: Any) -> None:
    """Keep a strong reference so the Qt dialog isn't garbage collected.

    Also (re)starts the event-pump tick timer — if the previous dialog
    had closed we'd unregistered it, so this is where interactive Zeno
    UI gets its main-thread heartbeat back.
    """
    _DIALOGS.append(dlg)

    try:
        def _on_destroyed(*_: Any) -> None:
            try:
                _DIALOGS.remove(dlg)
            except ValueError:
                pass

        widget = dlg.widget() if hasattr(dlg, "widget") else dlg
        widget.destroyed.connect(_on_destroyed)
    except Exception:
        pass

    _ensure_tick_timer()


def queue_main_thread(fn: Callable[[], None]) -> None:
    """Schedule ``fn`` for execution on Blender's main thread.

    Safe to call from any thread. Lazily (re)starts the drain timer so
    the callable runs even if the Zeno UI that triggered it has already
    closed.
    """
    _OPS_QUEUE.put(fn)
    _ensure_drain_timer()


def shutdown_qt_runtime() -> None:  # pragma: no cover - used on addon unregister
    """Tear down the Qt runtime cleanly on addon unregister."""
    global _APP_REF, _TICK_HANDLE, _OPS_DRAIN_HANDLE

    with _LOCK:
        for handle in (_TICK_HANDLE, _OPS_DRAIN_HANDLE):
            if handle is None:
                continue
            try:
                if bpy.app.timers.is_registered(handle):
                    bpy.app.timers.unregister(handle)
            except Exception:
                pass
        _TICK_HANDLE = None
        _OPS_DRAIN_HANDLE = None

        for dlg in list(_DIALOGS):
            try:
                widget = dlg.widget() if hasattr(dlg, "widget") else dlg
                widget.close()
            except Exception:
                pass
        _DIALOGS.clear()

        _APP_REF = None


__all__ = [
    "ensure_qt_runtime",
    "queue_main_thread",
    "retain_dialog",
    "shutdown_qt_runtime",
]
