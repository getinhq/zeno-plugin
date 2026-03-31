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

    def draw(self, context):  # pragma: no cover - Blender UI
        col = self.layout.column(align=True)
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

