from __future__ import annotations

import bpy

from .bridge import addon_prefs


class ZENO_MT_main(bpy.types.Menu):
    bl_label = "Zeno"
    bl_idname = "ZENO_MT_main"

    def draw(self, context):  # pragma: no cover - Blender runtime
        col = self.layout.column(align=True)
        col.operator("zeno.navigator_open", text="Navigator")
        col.separator()
        col.operator("chimera.palette_open", text="Command Palette")


class CHIMERA_PT_panel(bpy.types.Panel):
    bl_label = "Chimera"
    bl_idname = "CHIMERA_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Chimera"

    def draw(self, context):  # pragma: no cover - Blender runtime
        prefs = addon_prefs()
        layout = self.layout
        col = layout.column(align=True)
        if prefs:
            col.prop(prefs, "default_project", text="Project")
            col.prop(prefs, "default_asset", text="Asset")
        col.operator("chimera.load_asset", text="Load Latest")
        col.operator("chimera.publish_current_file", text="Publish Current .blend")
        col.separator()
        col.operator("chimera.palette_open", text="Command Palette (Ctrl+K)")


def _menu_func(self, context):  # pragma: no cover - Blender runtime
    self.layout.separator()
    self.layout.operator("chimera.load_asset", text="Chimera Load Asset")
    self.layout.operator("chimera.publish_current_file", text="Chimera Publish")
    self.layout.operator("chimera.palette_open", text="Chimera Command Palette")


def _topbar_zeno_menu(self, context):  # pragma: no cover - Blender runtime
    self.layout.menu("ZENO_MT_main", text="Zeno")


def register() -> None:
    bpy.utils.register_class(ZENO_MT_main)
    bpy.utils.register_class(CHIMERA_PT_panel)
    bpy.types.TOPBAR_MT_file.append(_menu_func)
    bpy.types.TOPBAR_MT_editor_menus.append(_topbar_zeno_menu)


def unregister() -> None:
    bpy.types.TOPBAR_MT_editor_menus.remove(_topbar_zeno_menu)
    bpy.types.TOPBAR_MT_file.remove(_menu_func)
    bpy.utils.unregister_class(CHIMERA_PT_panel)
    bpy.utils.unregister_class(ZENO_MT_main)

