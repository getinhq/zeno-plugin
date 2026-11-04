"""Review from Blender: submit a playblast for a task, read its notes, jump to their frames.

- ``zeno.submit_review``: renders the scene range as an H.264 playblast (viewport render when
  a 3D view is available, Workbench otherwise), restores every render setting it touched,
  and uploads it in the background as the task's next review revision. The scene's first
  frame and exact frame rate go with it, so reviewers' notes land on scene frame numbers.
  Notes the artist ticked as fixed are marked fixed *in that revision* once it is uploaded.
- ``zeno.review_notes``: popup of the task's unresolved notes; click a frame to jump there,
  tick a note as fixed, or drop all notes onto the timeline as markers. The docked version
  of this list is the sidebar panel in ``review_panel``.
"""
from __future__ import annotations

import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Any

import bpy

from zeno_client.review import MARKER_PREFIX, fps_string, marker_name, note_line, playblast_percentage

from . import review_state as state
from .bridge import addon_prefs, make_client

# --- playblast -------------------------------------------------------------------------


def _view3d_override(context):  # pragma: no cover - Blender runtime
    if bpy.app.background:
        return None
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region is not None:
                    return {"window": window, "area": area, "region": region}
    return None


def _set_video_output(render) -> None:  # pragma: no cover - Blender runtime
    settings = render.image_settings
    if hasattr(settings, "media_type"):  # Blender 4.5+/5.x split image vs. video output
        settings.media_type = "VIDEO"
    settings.file_format = "FFMPEG"
    render.ffmpeg.format = "MPEG4"
    render.ffmpeg.codec = "H264"
    render.ffmpeg.constant_rate_factor = "HIGH"


def playblast(context, out_dir: Path) -> Path:  # pragma: no cover - Blender runtime
    """Render the scene range to an MP4 in ``out_dir`` and return its path."""
    scene = context.scene
    render = scene.render
    settings = render.image_settings
    saved: dict[str, Any] = {
        "filepath": render.filepath,
        "use_file_extension": render.use_file_extension,
        "resolution_percentage": render.resolution_percentage,
        "engine": render.engine,
        "media_type": getattr(settings, "media_type", None),
        "file_format": settings.file_format,
        "ff_format": render.ffmpeg.format,
        "ff_codec": render.ffmpeg.codec,
        "ff_crf": render.ffmpeg.constant_rate_factor,
        "ff_audio": render.ffmpeg.audio_codec,
    }
    try:
        render.filepath = str(out_dir / "playblast_")
        render.use_file_extension = True
        render.resolution_percentage = playblast_percentage(render.resolution_x, render.resolution_percentage)
        _set_video_output(render)
        editor = getattr(scene, "sequence_editor", None)
        strips = (getattr(editor, "strips_all", None) or getattr(editor, "sequences_all", None) or []) if editor else []
        has_sound = any(s.type == "SOUND" for s in strips)
        render.ffmpeg.audio_codec = "AAC" if has_sound else "NONE"
        override = _view3d_override(context)
        if override:
            with context.temp_override(**override):
                bpy.ops.render.opengl(animation=True, view_context=True)
        else:
            render.engine = "BLENDER_WORKBENCH"
            bpy.ops.render.render(animation=True)
    finally:
        render.filepath = saved["filepath"]
        render.use_file_extension = saved["use_file_extension"]
        render.resolution_percentage = saved["resolution_percentage"]
        render.engine = saved["engine"]
        if saved["media_type"] is not None:
            settings.media_type = saved["media_type"]
        settings.file_format = saved["file_format"]
        render.ffmpeg.format = saved["ff_format"]
        render.ffmpeg.codec = saved["ff_codec"]
        render.ffmpeg.constant_rate_factor = saved["ff_crf"]
        render.ffmpeg.audio_codec = saved["ff_audio"]
    movies = sorted(out_dir.glob("playblast_*"), key=lambda p: p.stat().st_mtime)
    if not movies:
        raise RuntimeError("Blender did not write a playblast")
    return movies[-1]


# Metadata Blender writes into image files. Date/time/render time differ on every render, which
# would make unchanged frames hash differently and defeat per-frame dedup.
_VOLATILE_STAMPS = (
    "use_stamp_date", "use_stamp_time", "use_stamp_render_time", "use_stamp_memory",
    "use_stamp_hostname", "use_stamp_frame_range", "use_stamp_note",
)


def playblast_sequence(context, out_dir: Path) -> dict[int, Path]:  # pragma: no cover - Blender runtime
    """Render the scene range as PNG frames; returns {frame: path}. Settings are restored."""
    scene = context.scene
    render = scene.render
    settings = render.image_settings
    saved: dict[str, Any] = {
        "filepath": render.filepath,
        "use_file_extension": render.use_file_extension,
        "resolution_percentage": render.resolution_percentage,
        "engine": render.engine,
        "media_type": getattr(settings, "media_type", None),
        "file_format": settings.file_format,
        "color_mode": settings.color_mode,
        "compression": settings.compression,
        "stamps": {k: getattr(render, k) for k in _VOLATILE_STAMPS if hasattr(render, k)},
    }
    try:
        render.filepath = str(out_dir / "frame_")
        render.use_file_extension = True
        render.resolution_percentage = playblast_percentage(render.resolution_x, render.resolution_percentage)
        if hasattr(settings, "media_type"):
            settings.media_type = "IMAGE"
        settings.file_format = "PNG"
        settings.color_mode = "RGB"
        settings.compression = 15
        for key in saved["stamps"]:
            setattr(render, key, False)
        override = _view3d_override(context)
        if override:
            with context.temp_override(**override):
                bpy.ops.render.opengl(animation=True, view_context=True)
        else:
            render.engine = "BLENDER_WORKBENCH"
            bpy.ops.render.render(animation=True)
    finally:
        render.filepath = saved["filepath"]
        render.use_file_extension = saved["use_file_extension"]
        render.resolution_percentage = saved["resolution_percentage"]
        render.engine = saved["engine"]
        if saved["media_type"] is not None:
            settings.media_type = saved["media_type"]
        settings.file_format = saved["file_format"]
        settings.color_mode = saved["color_mode"]
        settings.compression = saved["compression"]
        for key, value in saved["stamps"].items():
            setattr(render, key, value)
    frames = {}
    for path in out_dir.glob("frame_*.png"):
        digits = path.stem.split("_")[-1]
        if digits.lstrip("-").isdigit():
            frames[int(digits)] = path
    if not frames:
        raise RuntimeError("Blender did not write any frames")
    return frames


def _dashboard_url() -> str:
    prefs = addon_prefs()
    url = str(getattr(prefs, "dashboard_url", "") or "").strip().rstrip("/") if prefs else ""
    return url or "http://localhost:5173"


class ZENO_OT_submit_review(bpy.types.Operator):
    bl_idname = "zeno.submit_review"
    bl_label = "Submit for Review"
    bl_description = "Playblast the scene range and send it to the task's reviewer"

    description: bpy.props.StringProperty(name="What changed?", default="")
    as_sequence: bpy.props.BoolProperty(
        name="As image sequence",
        description="Send PNG frames instead of a movie: frames that did not change since the last revision are not uploaded again",
        default=False,
    )
    open_after: bpy.props.BoolProperty(name="Open the review in the browser", default=False)

    def invoke(self, context, event):  # pragma: no cover - Blender runtime
        state.load_task_items()
        state.default_task(context.scene)
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):  # pragma: no cover - Blender runtime
        scene = context.scene
        col = self.layout.column()
        col.prop(scene, "zeno_review_task", text="Task")
        col.prop(self, "description")
        r = scene.render
        col.label(text=f"Frames {scene.frame_start}–{scene.frame_end} at {fps_string(r.fps, r.fps_base)} fps", icon="RENDER_ANIMATION")
        col.prop(self, "as_sequence")
        col.prop(self, "open_after")
        pending = state.pending_for(scene, state.current_task(scene))
        if pending:
            box = col.box()
            box.label(text=f"Marks {len(pending)} note(s) fixed in this revision:", icon="CHECKMARK")
            by_id = {n["id"]: n for n in state.notes["notes"]}
            for note_id in pending[:6]:
                n = by_id.get(note_id)
                box.label(text=note_line(n, width=56).strip() if n else note_id)
            if len(pending) > 6:
                box.label(text=f"…and {len(pending) - 6} more")

    def execute(self, context):  # pragma: no cover - Blender runtime
        scene = context.scene
        task_id = str(getattr(scene, "zeno_review_task", "") or "")
        if not task_id:
            self.report({"ERROR"}, "Pick the task this playblast is for.")
            return {"CANCELLED"}
        if self.as_sequence:
            # Ask before rendering: the studio may have image sequences switched off.
            try:
                decision = make_client().resolve_storage("frame.png")
            except Exception:  # noqa: BLE001 - older server: no storage workflow, allow
                decision = {}
            if decision.get("strategy") == "off":
                self.report({"ERROR"}, f"Image sequences are off ({decision.get('reason', '')}). Untick 'As image sequence'.")
                return {"CANCELLED"}
        out_dir = Path(tempfile.mkdtemp(prefix="zeno_playblast_"))
        fps = fps_string(scene.render.fps, scene.render.fps_base)
        description = self.description.strip() or None
        try:
            if self.as_sequence:
                frames = playblast_sequence(context, out_dir)
                kwargs = {"task_id": task_id, "frames": frames, "fps": fps, "description": description}
            else:
                movie = playblast(context, out_dir)
                kwargs = {"task_id": task_id, "file_path": movie, "frame_start": scene.frame_start,
                          "fps": fps, "description": description}
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Playblast failed: {exc}")
            return {"CANCELLED"}
        fixes = state.pending_for(scene, task_id)
        _start_upload(kwargs, open_after=self.open_after, sequence=self.as_sequence, fixes=fixes)
        self.report({"INFO"}, "Zeno: uploading playblast…")
        return {"FINISHED"}


def _start_upload(kwargs: dict, *, open_after: bool, sequence: bool = False, fixes: list[str] | tuple[str, ...] = ()) -> None:  # pragma: no cover - Blender runtime
    result: dict[str, Any] = {}
    client = make_client()
    task_id = kwargs["task_id"]

    def work() -> None:
        try:
            submit = client.submit_review_sequence if sequence else client.submit_review
            result["item"] = submit(**kwargs)
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)
            return
        item = result["item"]
        result["fixed"], result["fix_errors"] = state.send_fixes(
            client, list(fixes), item_id=item.get("id"), comment=f"Fixed in {item.get('label') or 'this revision'}"
        )

    thread = threading.Thread(target=work, name="zeno-review-upload", daemon=True)
    thread.start()

    def poll():
        if thread.is_alive():
            return 0.5
        wm = bpy.context.window_manager
        if "error" in result:
            msg = f"Upload failed: {result['error']}"
            wm.popup_menu(lambda self, _c: self.layout.label(text=msg), title="Zeno review", icon="ERROR")
            return None
        item = result["item"]
        scene = bpy.context.scene
        if scene is not None and result["fixed"]:
            left = [n for n in state.pending_for(scene, task_id) if n not in result["fixed"]]
            state.set_pending(scene, task_id, left)
        if scene is not None and state.current_task(scene) == task_id:
            state.refresh_async(task_id)
        url = f"{_dashboard_url()}/review/{item['id']}"
        print(f"[Zeno] submitted {item.get('label')} for review: {url}")
        if open_after:
            webbrowser.open(url)

        upload = item.get("upload") or {}

        def draw(self, _c):
            self.layout.label(text=f"{item.get('label', 'Revision')} is with the reviewer.")
            if upload.get("frames"):
                self.layout.label(
                    text=f"Uploaded {upload['uploaded_frames']} of {upload['frames']} frames; "
                    f"{upload['skipped_frames']} were already stored."
                )
            if result["fixed"]:
                self.layout.label(text=f"{len(result['fixed'])} note(s) marked fixed in this revision.", icon="CHECKMARK")
            if result["fix_errors"]:
                self.layout.label(text=f"{len(result['fix_errors'])} note(s) could not be updated; they stay ticked.", icon="ERROR")
            self.layout.operator("wm.url_open", text="Open in browser", icon="URL").url = url

        wm.popup_menu(draw, title="Zeno: submitted for review", icon="CHECKMARK")
        return None

    bpy.app.timers.register(poll, first_interval=0.5)


# --- notes -----------------------------------------------------------------------------


class ZENO_OT_review_notes(bpy.types.Operator):
    bl_idname = "zeno.review_notes"
    bl_label = "Review Notes"
    bl_description = "Unresolved review notes for your task; jump to their frames"

    def invoke(self, context, event):  # pragma: no cover - Blender runtime
        state.load_task_items()
        state.default_task(context.scene)
        state.fetch_notes(state.current_task(context.scene))
        return context.window_manager.invoke_popup(self, width=620)

    def draw(self, context):  # pragma: no cover - Blender runtime
        layout = self.layout
        row = layout.row()
        row.prop(context.scene, "zeno_review_task", text="Task")
        row.operator("zeno.review_notes_refresh", text="", icon="FILE_REFRESH")
        if state.notes["error"]:
            layout.label(text=state.notes["error"], icon="ERROR")
            return
        notes = state.notes["notes"]
        pending = set(state.pending_for(context.scene, state.current_task(context.scene)))
        if not notes:
            layout.label(text="No open notes. Nice.", icon="CHECKMARK")
            return
        box = layout.box()
        for n in notes:
            r = box.row(align=True)
            if n.get("frame") is not None:
                op = r.operator("zeno.review_goto_frame", text=str(n["frame"]), icon="PLAY")
                op.frame = int(n["frame"])
            r.label(text=note_line(n, width=60).strip().split("  ", 1)[-1])
            if n.get("status") == "open":
                ticked = n["id"] in pending
                r.operator("zeno.review_toggle_fix", text="Fixed" if ticked else "Fix", icon="CHECKBOX_HLT" if ticked else "CHECKBOX_DEHLT", depress=ticked).note_id = n["id"]
        layout.operator("zeno.review_markers", text="Show as timeline markers", icon="MARKER_HLT")

    def execute(self, context):  # pragma: no cover - Blender runtime
        return {"FINISHED"}


class ZENO_OT_review_notes_refresh(bpy.types.Operator):
    bl_idname = "zeno.review_notes_refresh"
    bl_label = "Refresh Review Notes"

    def execute(self, context):  # pragma: no cover - Blender runtime
        state.refresh_async(state.current_task(context.scene), with_tasks=True)
        return {"FINISHED"}


class ZENO_OT_review_goto_frame(bpy.types.Operator):
    bl_idname = "zeno.review_goto_frame"
    bl_label = "Go to Note Frame"

    frame: bpy.props.IntProperty()
    frame_end: bpy.props.IntProperty(default=-1)
    loop_range: bpy.props.BoolProperty(default=False, description="Also set the preview range to the note's frames")

    def execute(self, context):  # pragma: no cover - Blender runtime
        scene = context.scene
        if self.loop_range and self.frame_end > self.frame:
            scene.use_preview_range = True
            scene.frame_preview_start, scene.frame_preview_end = self.frame, self.frame_end
        scene.frame_set(self.frame)
        return {"FINISHED"}


class ZENO_OT_review_toggle_fix(bpy.types.Operator):
    bl_idname = "zeno.review_toggle_fix"
    bl_label = "Fixed in Next Submission"
    bl_description = "Tick when you have fixed this note; it is marked fixed in your next Submit for Review"

    note_id: bpy.props.StringProperty()

    def execute(self, context):  # pragma: no cover - Blender runtime
        scene = context.scene
        task_id = state.current_task(scene)
        pending = state.pending_for(scene, task_id)
        if self.note_id in pending:
            pending.remove(self.note_id)
        else:
            pending.append(self.note_id)
        state.set_pending(scene, task_id, pending)
        return {"FINISHED"}


class ZENO_OT_review_send_fixes(bpy.types.Operator):
    bl_idname = "zeno.review_send_fixes"
    bl_label = "Mark Fixed Without Submitting"
    bl_description = (
        "Mark the ticked notes fixed now, against the latest revision. Use this when the fix "
        "reaches review another way (a farm render, for example)"
    )

    def execute(self, context):  # pragma: no cover - Blender runtime
        scene = context.scene
        task_id = state.current_task(scene)
        pending = state.pending_for(scene, task_id)
        sent, failed = state.send_fixes(make_client(), pending, item_id=None, comment="Fixed in Blender")
        state.set_pending(scene, task_id, [n for n in pending if n not in sent])
        state.fetch_notes(task_id)
        if failed:
            self.report({"ERROR"}, f"{len(failed)} note(s) could not be updated: {failed[0]}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"{len(sent)} note(s) marked fixed. The reviewer verifies them on the next revision.")
        return {"FINISHED"}


class ZENO_OT_review_markers(bpy.types.Operator):
    bl_idname = "zeno.review_markers"
    bl_label = "Review Notes as Markers"
    bl_description = "Replace Zeno markers on the timeline with the task's unresolved notes"

    def execute(self, context):  # pragma: no cover - Blender runtime
        markers = context.scene.timeline_markers
        for m in [m for m in markers if m.name.startswith(MARKER_PREFIX)]:
            markers.remove(m)
        count = 0
        for n in state.notes["notes"]:
            if n.get("frame") is None:
                continue
            markers.new(name=marker_name(n), frame=int(n["frame"]))
            count += 1
        self.report({"INFO"}, f"{count} review note marker(s) on the timeline.")
        return {"FINISHED"}


_CLASSES = (
    ZENO_OT_submit_review,
    ZENO_OT_review_notes,
    ZENO_OT_review_notes_refresh,
    ZENO_OT_review_goto_frame,
    ZENO_OT_review_toggle_fix,
    ZENO_OT_review_send_fixes,
    ZENO_OT_review_markers,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
