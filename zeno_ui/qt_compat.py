"""Cross-DCC Qt binding shim.

Prefers ``qtpy`` (single abstraction over PySide2/PySide6/PyQt5/PyQt6), falls back to
direct ``PySide6`` then ``PySide2`` imports so the module works in hosts where only
one binding is pip-installed.
"""
from __future__ import annotations

from typing import Any


def _import_qt_via_qtpy() -> tuple[Any, Any, Any] | None:
    try:
        from qtpy import QtCore, QtGui, QtWidgets
    except Exception:
        return None
    return QtWidgets, QtCore, QtGui


def _import_qt_direct() -> tuple[Any, Any, Any]:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets

        return QtWidgets, QtCore, QtGui
    except ImportError:  # pragma: no cover - env specific
        from PySide2 import QtCore, QtGui, QtWidgets

        return QtWidgets, QtCore, QtGui


def get_qt_modules() -> tuple[Any, Any, Any]:
    """Return (QtWidgets, QtCore, QtGui); qtpy first, else direct PySide6/2."""
    via_qtpy = _import_qt_via_qtpy()
    if via_qtpy is not None:
        return via_qtpy
    return _import_qt_direct()


def qt_binding_name() -> str:
    """Human-readable Qt binding in use (for diagnostics)."""
    try:
        import qtpy

        return f"qtpy({qtpy.API_NAME})"
    except Exception:
        pass
    try:
        import PySide6  # noqa: F401

        return "PySide6"
    except ImportError:
        try:
            import PySide2  # noqa: F401

            return "PySide2"
        except ImportError:
            return "none"


def ensure_qapplication() -> Any:
    """Return the shared QApplication, creating one if needed.

    Sets ``setQuitOnLastWindowClosed(False)`` because hosts like Blender keep the
    QApplication alive across dialog close events.
    """
    import sys

    QtWidgets, _, _ = get_qt_modules()
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv or ["chimera"])
        try:
            app.setQuitOnLastWindowClosed(False)
        except Exception:  # pragma: no cover - older bindings
            pass
    return app


def get_main_qapplication() -> Any:
    QtWidgets, _, _ = get_qt_modules()
    app = QtWidgets.QApplication.instance()
    if app is None:
        raise RuntimeError("No QApplication; create one before showing Zeno dialogs.")
    return app


__all__ = [
    "ensure_qapplication",
    "get_main_qapplication",
    "get_qt_modules",
    "qt_binding_name",
]
