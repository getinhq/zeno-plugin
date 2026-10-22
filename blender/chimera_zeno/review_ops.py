"""Review from Blender: submit a playblast for a task, read its notes, jump to their frames.

- ``zeno.submit_review``: renders the scene range as an H.264 playblast (viewport render when
  a 3D view is available, Workbench otherwise), restores every render setting it touched,
  and uploads it in the background as the task's next review revision. The scene's first
  frame and exact frame rate go with it, so reviewers' notes land on scene frame numbers.
- ``zeno.review_notes``: popup of the task's unresolved notes; click a frame to jump there,
  mark a note fixed, or drop all notes onto the timeline as markers.
"""
from __future__ import annotations

import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Any

import bpy

from zeno_client.review import MARKER_PREFIX, fps_string, marker_name, note_line, playblast_percentage

from .bridge import addon_prefs, make_client

_task_items_cache: list[tuple[str, str, str]] = []
_notes_cache: dict[str, Any] = {"task_id": "", "notes": [], "error": ""}


def _launch_task_id() -> str:
    try:
        from zeno_client.launch_context import read_launch_context_from_environ

        ctx = read_launch_context_from_environ()
        return str(getattr(ctx, "task_id", "") or "") if ctx else ""
    except Exception:
        return ""


def _load_task_items() -> list[tuple[str, str, str]]:
    global _task_items_cache
    try:
        tasks = make_client().list_my_tasks()
    except Exception as exc:  # noqa: BLE001
        return [("", "Could not load tasks", str(exc))]
    items = []
    for t in tasks:
        label = str(t.get("title") or t.get("type") or t.get("id"))
        status = str(t.get("status") or "").replace("_", " ")
        items.append((str(t["id"]), f"{label}  ·  {status}", label))
    _task_items_cache = items or [("", "No tasks assigned to you", "")]
    return _task_items_cache


def _task_items(self, context):  # pragma: no cover - Blender runtime
    return _task_items_cache or [("", "No tasks loaded", "")]


def _default_task(scene) -> None:  # pragma: no cover - Blender runtime
    ids = [i[0] for i in _task_items_cache if i[0]]
    wanted = _launch_task_id()
    if wanted and wanted in ids:
        scene.zeno_review_task = wanted


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


def _dashboard_url() -> str:
    prefs = addon_prefs()
    url = str(getattr(prefs, "dashboard_url", "") or "").strip().rstrip("/") if prefs else ""
    return url or "http://localhost:5173"


class ZENO_OT_submit_review(bpy.types.Operator):
    bl_idname = "zeno.submit_review"
    bl_label = "Submit for Review"
    bl_description = "Playblast the scene range and send it to the task's reviewer"

    description: bpy.props.StringProperty(name="What changed?", default="")
    open_after: bpy.props.BoolProperty(name="Open the review in the browser", default=False)

    def invoke(self, context, event):  # pragma: no cover - Blender runtime
        _load_task_items()
        _default_task(context.scene)
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):  # pragma: no cover - Blender runtime
        scene = context.scene
        col = self.layout.column()
        col.prop(scene, "zeno_review_task", text="Task")
        col.prop(self, "description")
        r = scene.render
        col.label(text=f"Frames {scene.frame_start}–{scene.frame_end} at {fps_string(r.fps, r.fps_base)} fps", icon="RENDER_ANIMATION")
        col.prop(self, "open_after")

    def execute(self, context):  # pragma: no cover - Blender runtime
        scene = context.scene
        task_id = str(getattr(scene, "zeno_review_task", "") or "")
        if not task_id:
            self.report({"ERROR"}, "Pick the task this playblast is for.")
            return {"CANCELLED"}
        out_dir = Path(tempfile.mkdtemp(prefix="zeno_playblast_"))
        try:
            movie = playblast(context, out_dir)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Playblast failed: {exc}")
            return {"CANCELLED"}
        kwargs = {
            "task_id": task_id,
            "file_path": movie,
            "frame_start": scene.frame_start,
            "fps": fps_string(scene.render.fps, scene.render.fps_base),
            "description": self.description.strip() or None,
        }
        _start_upload(kwargs, open_after=self.open_after)
        self.report({"INFO"}, "Zeno: uploading playblast…")
        return {"FINISHED"}


def _start_upload(kwargs: dict, *, open_after: bool) -> None:  # pragma: no cover - Blender runtime
    result: dict[str, Any] = {}
    client = make_client()

    def work() -> None:
        try:
            result["item"] = client.submit_review(**kwargs)
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)

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
        url = f"{_dashboard_url()}/review/{item['id']}"
        print(f"[Zeno] submitted {item.get('label')} for review: {url}")
        if open_after:
            webbrowser.open(url)

        def draw(self, _c):
            self.layout.label(text=f"{item.get('label', 'Revision')} is with the reviewer.")
            self.layout.operator("wm.url_open", text="Open in browser", icon="URL").url = url

        wm.popup_menu(draw, title="Zeno: submitted for review", icon="CHECKMARK")
        return None

    bpy.app.timers.register(poll, first_interval=0.5)


# --- notes -----------------------------------------------------------------------------


def _fetch_notes(task_id: str) -> None:
    _notes_cache.update(task_id=task_id, notes=[], error="")
    if not task_id:
        return
    try:
        _notes_cache["notes"] = make_client().list_review_notes(task_id, status="unresolved")
    except Exception as exc:  # noqa: BLE001
        _notes_cache["error"] = str(exc)


class ZENO_OT_review_notes(bpy.types.Operator):
    bl_idname = "zeno.review_notes"
    bl_label = "Review Notes"
    bl_description = "Unresolved review notes for your task; jump to their frames"

    def invoke(self, context, event):  # pragma: no cover - Blender runtime
        _load_task_items()
        if not context.scene.zeno_review_task:
            _default_task(context.scene)
        _fetch_notes(str(context.scene.zeno_review_task or ""))
        return context.window_manager.invoke_popup(self, width=620)

    def draw(self, context):  # pragma: no cover - Blender runtime
        layout = self.layout
        row = layout.row()
        row.prop(context.scene, "zeno_review_task", text="Task")
        row.operator("zeno.review_notes_refresh", text="", icon="FILE_REFRESH")
        if _notes_cache["error"]:
            layout.label(text=_notes_cache["error"], icon="ERROR")
            return
        notes = _notes_cache["notes"]
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
                r.operator("zeno.review_note_fixed", text="Fixed", icon="CHECKMARK").note_id = n["id"]
        layout.operator("zeno.review_markers", text="Show as timeline markers", icon="MARKER_HLT")

    def execute(self, context):  # pragma: no cover - Blender runtime
        return {"FINISHED"}


class ZENO_OT_review_notes_refresh(bpy.types.Operator):
    bl_idname = "zeno.review_notes_refresh"
    bl_label = "Refresh Review Notes"

    def execute(self, context):  # pragma: no cover - Blender runtime
        _fetch_notes(str(context.scene.zeno_review_task or ""))
        return {"FINISHED"}


class ZENO_OT_review_goto_frame(bpy.types.Operator):
    bl_idname = "zeno.review_goto_frame"
    bl_label = "Go to Note Frame"

    frame: bpy.props.IntProperty()

    def execute(self, context):  # pragma: no cover - Blender runtime
        context.scene.frame_set(self.frame)
        return {"FINISHED"}


class ZENO_OT_review_note_fixed(bpy.types.Operator):
    bl_idname = "zeno.review_note_fixed"
    bl_label = "Mark Note Fixed"
    bl_description = "Tell the reviewer this note is fixed (they verify it on your next submission)"

    note_id: bpy.props.StringProperty()

    def execute(self, context):  # pragma: no cover - Blender runtime
        try:
            make_client().update_review_note(self.note_id, status="fixed", comment="Fixed in Blender")
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Could not update the note: {exc}")
            return {"CANCELLED"}
        _fetch_notes(str(context.scene.zeno_review_task or ""))
        self.report({"INFO"}, "Marked fixed. It is verified when the reviewer sees your next playblast.")
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
        for n in _notes_cache["notes"]:
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
    ZENO_OT_review_note_fixed,
    ZENO_OT_review_markers,
)


def register() -> None:
    bpy.types.Scene.zeno_review_task = bpy.props.EnumProperty(
        name="Task", items=_task_items, options={"SKIP_SAVE"}
    )
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, "zeno_review_task"):
        del bpy.types.Scene.zeno_review_task
