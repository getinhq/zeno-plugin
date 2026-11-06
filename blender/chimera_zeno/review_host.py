"""Blender side of the Zeno Review Notes window (``zeno_ui.review_notes_dialog``).

The window runs on Blender's main thread (Qt events are pumped from a ``bpy.app.timers``
tick), so these methods may touch ``bpy`` data directly. Operators and renders go through
``qt_host.queue_main_thread``, which runs them outside Qt's event processing (with a window
override where an operator needs one).
"""
from __future__ import annotations

from typing import Any

import bpy

from zeno_client.review import fps_string, playblast_percentage

from . import qt_host
from . import review_state as state


def _redraw_viewports() -> None:
    wm = bpy.context.window_manager
    for window in wm.windows if wm else []:
        for area in window.screen.areas:
            if area.type in ("VIEW_3D", "DOPESHEET_EDITOR", "TIMELINE"):
                area.tag_redraw()


def _run_with_window(fn) -> None:
    def call() -> None:
        wm = bpy.context.window_manager
        window = wm.windows[0] if wm and wm.windows else None
        if window is None:
            fn()
            return
        with bpy.context.temp_override(window=window, screen=window.screen):
            fn()

    qt_host.queue_main_thread(call)


class BlenderReviewHost:
    def current_frame(self) -> int:
        return int(bpy.context.scene.frame_current)

    def go_to(self, frame: int, frame_end: int | None) -> None:
        scene = bpy.context.scene
        if frame_end is not None and int(frame_end) > int(frame):
            scene.use_preview_range = True
            scene.frame_preview_start, scene.frame_preview_end = int(frame), int(frame_end)
        scene.frame_set(int(frame))

    def pending(self, task_id: str) -> list[str]:
        return state.pending_for(bpy.context.scene, task_id)

    def set_pending(self, task_id: str, note_ids: list[str]) -> None:
        state.set_pending(bpy.context.scene, task_id, note_ids)

    def on_tasks(self, tasks: list[dict[str, Any]]) -> None:
        state.set_task_items(tasks)

    def on_notes(self, task_id: str, notes: list[dict[str, Any]]) -> None:
        state.set_notes(task_id, notes)
        from . import task_status

        if task_id and str((task_status.status["task"] or {}).get("id") or "") != task_id:
            bpy.app.timers.register(lambda: (task_status.refresh(), None)[1], first_interval=0.1)
        scene = bpy.context.scene
        if task_id and task_id in [i[0] for i in state.task_items]:
            scene.zeno_review_task = task_id
        _redraw_viewports()

    def default_task_id(self, task_ids: list[str]) -> str:
        for candidate in (state.launch_task_id(), state.current_task(bpy.context.scene)):
            if candidate and candidate in task_ids:
                return candidate
        return ""

    def drawings_visible(self) -> bool:
        return bool(bpy.context.scene.zeno_show_drawings)

    def set_drawings_visible(self, on: bool) -> None:
        bpy.context.scene.zeno_show_drawings = bool(on)
        _redraw_viewports()

    def show_markers(self, notes: list[dict[str, Any]]) -> None:
        _run_with_window(lambda: bpy.ops.zeno.review_markers())

    def submit_review(self, task_id: str) -> None:
        if task_id and task_id in [i[0] for i in state.task_items]:
            bpy.context.scene.zeno_review_task = task_id
        from . import blender_qt

        if not blender_qt.show_submit_review_qt(task_id=task_id):
            _run_with_window(lambda: bpy.ops.zeno.submit_review("INVOKE_DEFAULT", keep_task=True, native=True))

    # --- Submit for Review window ------------------------------------------------------

    def scene_info(self) -> dict[str, Any]:
        scene = bpy.context.scene
        r = scene.render
        pct = playblast_percentage(r.resolution_x, r.resolution_percentage)
        cam = scene.camera
        return {
            "frame_start": int(scene.frame_start),
            "frame_end": int(scene.frame_end),
            "fps": fps_string(r.fps, r.fps_base),
            "has_camera": cam is not None and cam.type == "CAMERA",
            "resolution": f"{r.resolution_x * pct // 100}×{r.resolution_y * pct // 100}",
            "file_name": bpy.path.basename(bpy.data.filepath) or "unsaved file",
        }

    def scene_facts(self) -> dict[str, Any]:
        from .scene_facts import collect_facts

        return collect_facts(bpy.context.scene)

    def apply_fix(self, fix: dict[str, Any], on_done) -> None:
        """Run a check's fix that changes the file (``update_asset``), then ``on_done(ok, message)``."""
        if fix.get("action") != "update_asset":
            on_done(False, f"Unknown fix {fix.get('action')}")
            return

        def run() -> None:
            ret = bpy.ops.chimera.update_linked_asset(
                library=str(fix.get("library") or ""), asset=str(fix.get("asset_code") or ""),
                version=str(fix.get("version") or ""), representation=str(fix.get("representation") or "blend"),
            )
            ok = "FINISHED" in ret
            try:
                on_done(ok, f"{fix.get('asset_code')} updated to v{int(fix.get('version') or 0):03d}." if ok
                        else f"Could not update {fix.get('asset_code')}; see Blender's info bar.")
            except RuntimeError:  # window closed meanwhile
                pass

        _run_with_window(run)

    def set_frame_range(self, start: int, end: int) -> None:
        scene = bpy.context.scene
        scene.frame_start, scene.frame_end = int(start), int(end)
        if not (scene.frame_start <= scene.frame_current <= scene.frame_end):
            scene.frame_set(scene.frame_start)

    def render_and_submit(
        self, task_id: str, *, description: str, as_sequence: bool, fixes: list[str], on_done, on_stage=None
    ) -> None:
        """Render (blocks Blender, as any playblast does), then upload in the background.

        The render is queued to run outside Qt's event processing: rendering inside a Qt slot
        nests Blender's Cocoa autorelease pools inside Qt's and crashes Blender on macOS.
        """
        from . import review_ops

        def done(result: dict[str, Any]) -> None:
            try:
                on_done(result)
            except RuntimeError:  # the window was closed while uploading
                print(f"[Zeno] review upload finished: {result.get('url') or result.get('error')}")

        def render() -> None:
            try:
                kwargs = review_ops.render_playblast(bpy.context, task_id, as_sequence=as_sequence, description=description)
            except Exception as exc:  # noqa: BLE001
                done({"error": f"Playblast failed: {exc}"})
                return
            if on_stage is not None:
                try:
                    on_stage("uploading")
                except RuntimeError:
                    pass
            review_ops.start_upload(kwargs, sequence=as_sequence, fixes=fixes, on_done=done)

        qt_host.queue_main_thread(render)
