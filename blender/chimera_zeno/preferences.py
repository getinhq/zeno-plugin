from __future__ import annotations

import bpy


class ChimeraAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__.split(".")[0]

    api_base_url: bpy.props.StringProperty(name="API Base URL", default="http://127.0.0.1:8000")
    default_project: bpy.props.StringProperty(name="Default Project", default="ndfc")
    default_asset: bpy.props.StringProperty(name="Default Asset", default="")
    user_id: bpy.props.StringProperty(name="User ID", default="blender_user")
    session_id: bpy.props.StringProperty(name="Session ID", default="")
    cache_max_gb: bpy.props.IntProperty(name="Cache Max GiB", default=50, min=1, soft_max=500)
    use_omni_publish: bpy.props.BoolProperty(name="Use Omni Publish", default=True)
    open_after_load: bpy.props.BoolProperty(name="Open Main File After Load", default=True)
    use_dashboard_qt_ui: bpy.props.BoolProperty(
        name="Dashboard UI (Qt)",
        description=(
            "Use the Zeno-themed Qt windows for Command Palette and Navigator when PySide is "
            "available. Install into Blender's Python: pip install PySide6. "
            "If disabled or PySide is missing, Blender's built-in dialogs are used."
        ),
        default=True,
    )
    use_hub_mode: bpy.props.BoolProperty(
        name="Use Chimera Hub (recommended)",
        description=(
            "Route publish/load/UI through the resident Chimera hub process so this addon "
            "can stay tiny (stdlib-only) and the heavy work runs in one shared Python env. "
            "If disabled or the hub is unreachable, the addon falls back to in-process mode."
        ),
        default=False,
    )
    hub_python_exe: bpy.props.StringProperty(
        name="Hub Python Interpreter",
        description=(
            "Path to the Python that has 'chimera_hub' installed. Leave blank to autodiscover "
            "via $CHIMERA_HUB_PYTHON or $CHIMERA_ROOT/venv/bin/python."
        ),
        default="",
    )
    auto_install_pyside: bpy.props.BoolProperty(
        name="Auto-install PySide6 in Blender",
        description=(
            "On first launch, silently pip-install PySide6 into Blender's bundled Python "
            "if it is missing. Required only for the in-DCC Qt fallback path."
        ),
        default=True,
    )

    def draw(self, context):  # pragma: no cover - Blender UI
        layout = self.layout

        box = layout.box()
        box.label(text="Hub mode (recommended)")
        box.prop(self, "use_hub_mode")
        box.prop(self, "hub_python_exe")

        qt_box = layout.box()
        qt_box.label(text="Dashboard UI (Qt) — Blender-side fallback")
        qt_box.prop(self, "use_dashboard_qt_ui")
        qt_box.prop(self, "auto_install_pyside")
        row = qt_box.row(align=True)
        row.operator("chimera.check_qt_status", icon="INFO")
        row.operator("chimera.install_pyside", icon="IMPORT")

        col = layout.column(align=True)
        col.label(text="API / cache")
        col.prop(self, "api_base_url")
        col.prop(self, "default_project")
        col.prop(self, "default_asset")
        col.prop(self, "user_id")
        col.prop(self, "cache_max_gb")
        col.prop(self, "use_omni_publish")
        col.prop(self, "open_after_load")


def register() -> None:
    bpy.utils.register_class(ChimeraAddonPreferences)


def unregister() -> None:
    bpy.utils.unregister_class(ChimeraAddonPreferences)

