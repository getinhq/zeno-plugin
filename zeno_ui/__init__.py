"""Zeno dashboard-aligned Qt styling and shared DCC UI helpers."""

from zeno_ui.stylesheet import apply_stylesheet, load_stylesheet
from zeno_ui.workflows import (
    PaletteRefreshResult,
    decode_project,
    format_project_enum,
    list_assets_for_project,
    list_projects_for_navigator,
    navigator_launch_hint_enum,
    refresh_palette_state,
    resolve_palette_default_project,
    sanitize_palette_fields,
)

__all__ = [
    "PaletteRefreshResult",
    "apply_stylesheet",
    "decode_project",
    "format_project_enum",
    "list_assets_for_project",
    "list_projects_for_navigator",
    "load_stylesheet",
    "navigator_launch_hint_enum",
    "refresh_palette_state",
    "resolve_palette_default_project",
    "sanitize_palette_fields",
]


def __getattr__(name: str):
    if name in ("ZenoNavigatorDialog", "ZenoPaletteDialog"):
        from zeno_ui.qt_dialogs import ZenoNavigatorDialog, ZenoPaletteDialog

        return ZenoNavigatorDialog if name == "ZenoNavigatorDialog" else ZenoPaletteDialog
    raise AttributeError(name)
