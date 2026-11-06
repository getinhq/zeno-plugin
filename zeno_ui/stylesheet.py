from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

_FONT_FILES = ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf")
_FONTS_LOADED = False


def _flatten_tokens(obj: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, val in obj.items():
        path = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(val, dict):
            out.update(_flatten_tokens(val, path))
        else:
            out[path] = str(val)
    return out


@lru_cache(maxsize=4)
def _load_theme_json() -> dict[str, str]:
    pkg = resources.files("zeno_ui")
    data = (pkg / "theme.json").read_text(encoding="utf-8")
    raw = json.loads(data)
    return _flatten_tokens(raw)


def _load_base_qss() -> str:
    pkg = resources.files("zeno_ui")
    return (pkg / "styles" / "base.qss").read_text(encoding="utf-8")


def _icons_dir() -> str:
    # QSS wants forward slashes, also on Windows.
    return str(resources.files("zeno_ui") / "styles" / "icons").replace("\\", "/")


@lru_cache(maxsize=1)
def load_stylesheet() -> str:
    """Return dashboard-aligned QSS with theme tokens substituted."""
    tokens = dict(_load_theme_json())
    tokens["path.icons"] = _icons_dir()
    qss = _load_base_qss()
    for key, val in tokens.items():
        qss = qss.replace("{" + key + "}", val)
    return qss


def theme_tokens() -> dict[str, str]:
    """Flattened theme.json tokens (``color.primary`` → ``#d4ff00``) for per-widget styles."""
    return dict(_load_theme_json())


def ensure_fonts() -> None:
    """Register the bundled Space Mono with Qt (once; needs a QApplication).

    The dashboard sets everything in Space Mono. Without registering it Qt falls back
    to the system font and, on macOS, spends a noticeable moment populating font
    aliases the first time a window asks for the missing family.
    """
    global _FONTS_LOADED
    if _FONTS_LOADED:
        return
    from zeno_ui.qt_compat import get_qt_modules

    _, _, QtGui = get_qt_modules()
    fonts = resources.files("zeno_ui") / "fonts"
    for name in _FONT_FILES:
        try:
            QtGui.QFontDatabase.addApplicationFont(str(fonts / name))
        except Exception as exc:  # pragma: no cover - env specific
            print(f"[zeno_ui] could not load font {name}: {exc}")
    _FONTS_LOADED = True


def apply_stylesheet(widget: Any, extra_qss: str = "") -> None:
    """Apply Zeno theme QSS (plus optional widget-specific rules) to a QWidget or QApplication."""
    ensure_fonts()
    widget.setStyleSheet(load_stylesheet() + ("\n" + extra_qss if extra_qss else ""))


def field_label(text: str) -> Any:
    """A form label the way the dashboard draws them: small, uppercase, muted."""
    from zeno_ui.qt_compat import get_qt_modules

    QtWidgets, _, _ = get_qt_modules()
    label = QtWidgets.QLabel(text.upper())
    label.setObjectName("fieldLabel")
    return label


def warm_up() -> None:
    """Do the one-time work behind the first Zeno window ahead of time.

    Registers the fonts, builds the stylesheet and lays out a hidden themed dialog,
    so Qt has parsed the QSS and loaded the glyphs and icons before an artist clicks.
    """
    from zeno_ui.qt_compat import get_qt_modules

    QtWidgets, _, _ = get_qt_modules()
    dlg = QtWidgets.QDialog()
    apply_stylesheet(dlg)
    lay = QtWidgets.QVBoxLayout(dlg)
    lay.addWidget(field_label("Project"))
    lay.addWidget(QtWidgets.QComboBox(dlg))
    lay.addWidget(QtWidgets.QLineEdit(dlg))
    lay.addWidget(QtWidgets.QCheckBox("ready", dlg))
    lay.addWidget(QtWidgets.QPushButton("Run", dlg))
    dlg.ensurePolished()
    lay.activate()
    for child in dlg.findChildren(QtWidgets.QWidget):
        child.ensurePolished()
        child.sizeHint()
    dlg.deleteLater()


__all__ = ["apply_stylesheet", "ensure_fonts", "field_label", "load_stylesheet", "theme_tokens", "warm_up"]
