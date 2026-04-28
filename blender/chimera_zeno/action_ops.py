from __future__ import annotations

import bpy

from . import blender_qt
from . import launch_context as launch_context_mod


class ZENO_OT_version_switcher_open(bpy.types.Operator):
    bl_idname = "zeno.version_switcher_open"
    bl_label = "Open Zeno Version Switcher"
    bl_description = "Switch version for current entity context"

    def invoke(self, context, event):  # pragma: no cover - Blender runtime
        if blender_qt.show_version_switcher_qt(operator=self):
            return {"FINISHED"}
        self.report({"ERROR"}, "Version Switcher requires Qt UI runtime.")
        return {"CANCELLED"}


class ZENO_OT_report_issue_open(bpy.types.Operator):
    bl_idname = "zeno.report_issue_open"
    bl_label = "Open Zeno Report Issue"
    bl_description = "Raise a ticket for the current context"

    def invoke(self, context, event):  # pragma: no cover - Blender runtime
        if blender_qt.show_report_issue_qt(operator=self):
            return {"FINISHED"}
        self.report({"ERROR"}, "Report Issue requires Qt UI runtime.")
        return {"CANCELLED"}


class ZENO_OT_publisher_open(bpy.types.Operator):
    bl_idname = "zeno.publisher_open"
    bl_label = "Open Zeno Publisher"
    bl_description = "Publish current file for current context"

    def invoke(self, context, event):  # pragma: no cover - Blender runtime
        if blender_qt.show_publisher_qt(operator=self):
            return {"FINISHED"}
        hint = launch_context_mod.get_session_launch_hint() or {}
        project = str(hint.get("project_code") or "").strip()
        asset = str(hint.get("asset_code") or "").strip()
        if not asset:
            aid = str(hint.get("asset_id") or "").strip()
            # No safe project->asset lookup in native fallback; ask user to use Qt.
            if aid:
                self.report({"ERROR"}, "Publisher fallback unavailable without Qt. Enable Qt UI.")
            else:
                self.report({"ERROR"}, "Missing asset context. Open from an entity context.")
            return {"CANCELLED"}
        return bpy.ops.chimera.publish_current_file(
            "INVOKE_DEFAULT",
            project=project,
            asset=asset,
            version="next",
            representation="blend",
        )


def register() -> None:
    bpy.utils.register_class(ZENO_OT_version_switcher_open)
    bpy.utils.register_class(ZENO_OT_report_issue_open)
    bpy.utils.register_class(ZENO_OT_publisher_open)


def unregister() -> None:
    bpy.utils.unregister_class(ZENO_OT_publisher_open)
    bpy.utils.unregister_class(ZENO_OT_report_issue_open)
    bpy.utils.unregister_class(ZENO_OT_version_switcher_open)

