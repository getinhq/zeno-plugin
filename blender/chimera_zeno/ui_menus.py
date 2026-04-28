from __future__ import annotations

import bpy


class ZENO_MT_main(bpy.types.Menu):
    bl_label = "Zeno"
    bl_idname = "ZENO_MT_main"

    def draw(self, context):  # pragma: no cover - Blender runtime
        col = self.layout.column(align=True)
        col.operator("zeno.navigator_open", text="Navigator")
        col.operator("zeno.version_switcher_open", text="Version Switcher")
        col.operator("zeno.report_issue_open", text="Report Issue")
        col.operator("zeno.publisher_open", text="Publisher")
        col.separator()
        col.operator("chimera.palette_open", text="Command Palette")


def _topbar_zeno_menu(self, context):  # pragma: no cover - Blender runtime
    self.layout.menu("ZENO_MT_main", text="Zeno")


def register() -> None:
    # Everything is reachable from the single top-bar "Zeno" menu — no
    # sidebar tabs and no File-menu injections, so the Blender UI stays
    # clean and matches the top-down entry point the artist expects.
    bpy.utils.register_class(ZENO_MT_main)
    bpy.types.TOPBAR_MT_editor_menus.append(_topbar_zeno_menu)


def unregister() -> None:
    bpy.types.TOPBAR_MT_editor_menus.remove(_topbar_zeno_menu)
    bpy.utils.unregister_class(ZENO_MT_main)
