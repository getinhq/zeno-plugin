"""Blender side of the Zeno Review Notes window (``zeno_ui.review_notes_dialog``).

The window runs on Blender's main thread (Qt events are pumped from a ``bpy.app.timers``
tick), so these methods may touch ``bpy`` data directly. Operators that open dialogs go
through ``qt_host.queue_main_thread`` with a window override, because a timer has none.
"""
from __future__ import annotations

from typing import Any

import bpy

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
        _run_with_window(lambda: bpy.ops.zeno.submit_review("INVOKE_DEFAULT", keep_task=True))
