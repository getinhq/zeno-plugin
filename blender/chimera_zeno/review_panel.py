"""Docked review notes: View3D sidebar > Zeno.

The panel lists the selected task's unresolved notes, newest revision context included, and
keeps itself up to date in the background. Clicking a note's frames jumps there (and loops the
range); ticking *Fixed* queues the note for the next Submit for Review.
"""
from __future__ import annotations

import textwrap

import bpy

from zeno_client.review import adjacent_note_frame, notes_at_frame

from . import review_state as state
from .bridge import addon_prefs

_STATUS_ICON = {"open": "RADIOBUT_OFF", "fixed": "RADIOBUT_ON", "verified": "CHECKMARK"}


def _frames_label(note) -> str:
    start, end = note.get("frame"), note.get("frame_end")
    if start is None:
        return "Whole clip"
    return f"{start}–{end}" if end not in (None, start) else str(start)


def _sort_key(note):
    return (note.get("frame") is None, note.get("frame") or 0, note.get("created_at") or "")


class ZENO_OT_review_step_note(bpy.types.Operator):
    bl_idname = "zeno.review_step_note"
    bl_label = "Next Review Note"
    bl_description = "Jump to the next (or previous) frame that has a review note"

    direction: bpy.props.IntProperty(default=1)

    def execute(self, context):  # pragma: no cover - Blender runtime
        frame = adjacent_note_frame(state.notes["notes"], context.scene.frame_current, self.direction)
        if frame is None:
            self.report({"INFO"}, "No more notes in that direction.")
            return {"CANCELLED"}
        context.scene.frame_set(frame)
        return {"FINISHED"}


class ZENO_PT_review_notes(bpy.types.Panel):
    bl_idname = "ZENO_PT_review_notes"
    bl_label = "Review Notes"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Zeno"

    @classmethod
    def poll(cls, context):  # pragma: no cover - Blender runtime
        prefs = addon_prefs()
        return bool(getattr(prefs, "show_review_panel", True)) if prefs else True

    def draw(self, context):  # pragma: no cover - Blender runtime
        layout = self.layout
        scene = context.scene
        task_id = state.current_task(scene)
        state.ensure_fresh(task_id)

        row = layout.row(align=True)
        row.prop(scene, "zeno_review_task", text="")
        row.operator("zeno.review_notes_refresh", text="", icon="FILE_REFRESH")

        if state.notes["error"]:
            layout.label(text=state.notes["error"][:90], icon="ERROR")
        if not task_id:
            layout.label(text="Pick your task to see its notes.", icon="INFO")
            return
        if state.notes["task_id"] != task_id:
            layout.label(text="Loading notes…", icon="SORTTIME")
            return

        notes = sorted(state.notes["notes"], key=_sort_key)
        pending = set(state.pending_for(scene, task_id))
        open_n = sum(1 for n in notes if n.get("status") == "open")
        fixed_n = sum(1 for n in notes if n.get("status") == "fixed")
        summary = f"{open_n} open · {fixed_n} waiting for the reviewer"
        if pending:
            summary += f" · {len(pending)} ticked"
        layout.label(text=summary)

        row = layout.row(align=True)
        row.operator("zeno.review_step_note", text="", icon="TRIA_LEFT").direction = -1
        row.label(text=f"Frame {scene.frame_current}")
        row.operator("zeno.review_step_note", text="", icon="TRIA_RIGHT").direction = 1
        row.prop(scene, "zeno_show_drawings", text="", icon="GREASEPENCIL")

        if not notes:
            layout.label(text="No open notes. Nice.", icon="CHECKMARK")
            return

        here = {n["id"] for n in notes_at_frame(notes, scene.frame_current)}
        width = max(24, int(context.region.width / (7 * context.preferences.system.ui_scale)))
        for n in notes:
            self._draw_note(layout.box(), n, here=n["id"] in here, ticked=n["id"] in pending, width=width)

        if pending:
            box = layout.box()
            box.label(text=f"{len(pending)} ticked: sent with your next submission.", icon="CHECKMARK")
            col = box.column(align=True)
            col.operator("zeno.submit_review", text="Submit for Review…", icon="RENDER_ANIMATION")
            col.operator("zeno.review_send_fixes", text="Mark fixed without submitting")
        layout.operator("zeno.review_markers", text="Show as timeline markers", icon="MARKER_HLT")

    @staticmethod
    def _draw_note(box, n, *, here: bool, ticked: bool, width: int) -> None:  # pragma: no cover - Blender runtime
        head = box.row(align=True)
        if n.get("frame") is not None:
            op = head.operator("zeno.review_goto_frame", text=_frames_label(n), icon="PLAY", depress=here)
            op.frame = int(n["frame"])
            op.frame_end = int(n["frame_end"]) if n.get("frame_end") is not None else -1
            op.loop_range = n.get("frame_end") not in (None, n.get("frame"))
        else:
            head.label(text=_frames_label(n))
        who = n.get("author_name") or "Someone"
        if n.get("raised_in"):
            who += f" · {n['raised_in']}"
        head.label(text=who, icon=_STATUS_ICON.get(n.get("status"), "DOT"))
        if (n.get("drawing") or {}).get("shapes"):
            head.label(text="", icon="GREASEPENCIL")

        col = box.column(align=True)
        for line in textwrap.wrap(" ".join(str(n.get("body") or "(drawing only)").split()), width)[:8]:
            col.label(text=line)
        for reply in (n.get("replies") or [])[-2:]:
            if reply.get("body"):
                text = f"↳ {reply.get('author_name') or 'Someone'}: {' '.join(reply['body'].split())}"
                col.label(text=textwrap.shorten(text, width + 4, placeholder="…"))

        if n.get("status") == "open":
            op = box.operator(
                "zeno.review_toggle_fix",
                text="Fixed: goes with next submission" if ticked else "Tick when fixed",
                icon="CHECKBOX_HLT" if ticked else "CHECKBOX_DEHLT",
                depress=ticked,
            )
            op.note_id = n["id"]
        elif n.get("status") == "fixed":
            where = f" in {n['fixed_in']}" if n.get("fixed_in") else ""
            box.label(text=f"Fixed{where}, waiting for the reviewer", icon="TIME")


_CLASSES = (ZENO_OT_review_step_note, ZENO_PT_review_notes)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
