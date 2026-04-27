from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any


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


def load_stylesheet() -> str:
    """Return dashboard-aligned QSS with theme tokens substituted."""
    tokens = _load_theme_json()
    qss = _load_base_qss()
    for key, val in tokens.items():
        qss = qss.replace("{" + key + "}", val)
    return qss


def apply_stylesheet(widget: Any) -> None:
    """Apply Zeno theme QSS to a QWidget (or QApplication)."""
    widget.setStyleSheet(load_stylesheet())


__all__ = ["apply_stylesheet", "load_stylesheet"]
