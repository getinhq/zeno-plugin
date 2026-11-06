"""Zeno status in Blender's top bar, on the right before the Scene and View Layer pickers.

``sh0010 lighting · v003 changes requested · 2 open notes · due tomorrow · 3 new``

Refreshed on a worker thread every ``REFRESH_SECONDS`` (and after a submission or file load),
so a reviewer's decision shows up while the artist works. Clicking the task opens Review
Notes; "N new" lists the unread notifications. Blender draws the top bar itself, so this is
native UI; the windows it opens are the Zeno ones.
"""
from __future__ import annotations

import threading
import time
import webbrowser
from datetime import date
from typing import Any

import bpy
from bpy.app.handlers import persistent

from zeno_client.review import notification_text, status_segments

from . import review_state as state
from .bridge import addon_prefs, make_client

REFRESH_SECONDS = 90.0

status: dict[str, Any] = {
    "task": None, "last": None, "open_notes": 0, "unread": 0, "items": [], "error": "", "fetched_at": 0.0,
}
_worker: threading.Thread | None = None


def enabled() -> bool:
    prefs = addon_prefs()
    return bool(getattr(prefs, "show_status_line", True)) if prefs else True


_drawn = ""  # what the top bar last showed, so it is only nudged when that changes


def _segments() -> list[tuple[str, str]]:
    return status_segments(
        status["task"], last=status["last"], open_notes=status["open_notes"], unread=status["unread"], today=date.today()
    )


def _tag_topbar() -> None:  # pragma: no cover - Blender runtime
    """Redraw the top bar when the status changed.

    The top bar is a global area: it is not in ``screen.areas``, so ``tag_redraw`` can't reach
    it, and it only redraws on the notifiers it listens to. Re-assigning the view layer's name
    sends one of those (scene/layer) without dirtying the file, adding an undo step or
    re-evaluating objects, so it is the cheapest nudge; it runs only when the text changes.
    """
    global _drawn
    shown = repr((_segments(), bool(status["error"]))) if enabled() else ""
    if shown == _drawn:
        return
    _drawn = shown
    wm = bpy.context.window_manager
    window = wm.windows[0] if wm and wm.windows else None
    if window is None:
        return
    try:
        layer = window.view_layer
        layer.name = layer.name
    except Exception:  # noqa: BLE001 - redraw happens on the next user interaction instead
        pass


def refresh() -> None:  # pragma: no cover - Blender runtime
    """Fetch the current task's state without blocking Blender."""
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    scene = bpy.context.scene
    wanted = [t for t in (state.current_task(scene) if scene else "", state.launch_task_id()) if t]
    result: dict[str, Any] = {}
    client = make_client()

    def work() -> None:
        try:
            tasks = client.list_my_tasks()
            result["tasks"] = tasks
            by_id = {str(t.get("id")): t for t in tasks}
            task = next((by_id[t] for t in wanted if t in by_id), None)
            if task is None:  # not opened from Zeno and nothing picked: an only active task is unambiguous
                active = [t for t in tasks if str(t.get("status_raw") or t.get("status") or "") == "in_progress"]
                task = active[0] if len(active) == 1 else None
            result["task"] = task
            if task is None:
                return
            tid = str(task["id"])
            result["last"] = client.latest_review_item(tid)
            result["open_notes"] = sum(1 for n in client.list_review_notes(tid, status="open"))
            if task.get("project_id"):
                inbox = client.list_notifications(str(task["project_id"]), unread_only=True, limit=12)
                result["items"] = list(inbox.get("items") or [])
                result["unread"] = int(inbox.get("unread_count") or 0)
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)

    _worker = threading.Thread(target=work, name="zeno-task-status", daemon=True)
    _worker.start()

    def poll():
        if _worker is not None and _worker.is_alive():
            return 0.5
        status["fetched_at"] = time.monotonic()
        status["error"] = result.get("error", "")
        if "tasks" in result:
            if not state.task_items:
                state.set_task_items(result["tasks"])
            task = result.get("task")
            status.update(task=task, last=result.get("last"), open_notes=result.get("open_notes", 0),
                          unread=result.get("unread", 0), items=result.get("items", []))
            scene = bpy.context.scene
            if task is not None and scene is not None and not state.current_task(scene):
                if str(task["id"]) in [i[0] for i in state.task_items]:
                    scene.zeno_review_task = str(task["id"])
        _tag_topbar()
        return None

    bpy.app.timers.register(poll, first_interval=0.5)


def _tick() -> float:  # pragma: no cover - Blender runtime
    if enabled():
        refresh()
    return REFRESH_SECONDS


@persistent
def _on_load(_dummy) -> None:  # pragma: no cover - Blender runtime
    status.update(task=None, last=None, open_notes=0, unread=0, items=[])
    bpy.app.timers.register(lambda: (refresh(), None)[1], first_interval=1.0)


def _mark_read_async(ids: list[str]) -> None:  # pragma: no cover - Blender runtime
    client = make_client()

    def work() -> None:
        try:
            client.mark_notifications_read(ids)
        except Exception as exc:  # noqa: BLE001
            print(f"[Zeno] could not mark notifications read: {exc}")

    threading.Thread(target=work, name="zeno-mark-read", daemon=True).start()
    gone = set(ids)
    status["items"] = [n for n in status["items"] if n.get("id") not in gone]
    status["unread"] = max(0, status["unread"] - len(gone))
    _tag_topbar()


# --- UI --------------------------------------------------------------------------------


def draw_status(self, context) -> None:  # pragma: no cover - Blender runtime
    # The upper bar draws its left region (File … Zeno) at a fixed width that clips anything
    # appended, so the status goes in the right region, which grows to fit.
    if not enabled() or context.region is None or context.region.alignment != "RIGHT":
        return
    segs = _segments()
    if not segs:
        return
    layout = self.layout
    row = layout.row(align=True)
    title, _ = segs[0]
    row.operator("zeno.review_notes", text=title, icon="TEXT", emboss=False)
    for text, tone in segs[1:]:
        if status["unread"] and text.endswith(" new"):
            cell = row.row(align=True)
            cell.ui_units_x = 1.6 + 0.5 * len(text)  # header menus are sized too narrow and clip "3 new"
            cell.menu("ZENO_MT_inbox", text=text, icon="INTERNET")
            continue
        cell = row.row(align=True)
        cell.alert = tone == "danger"  # Blender headers have one accent: red, for notes and lateness
        cell.label(text=f"· {text}")
    if status["error"]:
        row.label(text="· offline", icon="UNLINKED")
    layout.separator(factor=2.0)


class ZENO_MT_inbox(bpy.types.Menu):
    bl_idname = "ZENO_MT_inbox"
    bl_label = "Zeno: new"

    def draw(self, context):  # pragma: no cover - Blender runtime
        layout = self.layout
        if not status["items"]:
            layout.label(text="Nothing new.")
            return
        for n in status["items"]:
            op = layout.operator("zeno.open_notification", text=notification_text(n, width=80), icon="DOT")
            op.notification_id = str(n.get("id") or "")
            payload = n.get("payload") or {}
            op.task_id = str(payload.get("task_id") or "")
            op.item_id = str(payload.get("item_id") or "")
        layout.separator()
        layout.operator("zeno.mark_notifications_read", text="Mark all read", icon="CHECKMARK")


class ZENO_OT_open_notification(bpy.types.Operator):
    bl_idname = "zeno.open_notification"
    bl_label = "Open Notification"
    bl_description = "Notes for your current task open in Review Notes; anything else opens in the browser"

    notification_id: bpy.props.StringProperty(options={"HIDDEN"})
    task_id: bpy.props.StringProperty(options={"HIDDEN"})
    item_id: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):  # pragma: no cover - Blender runtime
        if self.notification_id:
            _mark_read_async([self.notification_id])
        if self.task_id and self.task_id == state.current_task(context.scene):
            return bpy.ops.zeno.review_notes("INVOKE_DEFAULT")
        if self.item_id:
            from .review_ops import review_url

            webbrowser.open(review_url(self.item_id))
        return {"FINISHED"}


class ZENO_OT_mark_notifications_read(bpy.types.Operator):
    bl_idname = "zeno.mark_notifications_read"
    bl_label = "Mark Zeno Notifications Read"

    def execute(self, context):  # pragma: no cover - Blender runtime
        ids = [str(n.get("id")) for n in status["items"] if n.get("id")]
        if ids:
            _mark_read_async(ids)
        status["unread"] = 0
        return {"FINISHED"}


_CLASSES = (ZENO_MT_inbox, ZENO_OT_open_notification, ZENO_OT_mark_notifications_read)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_HT_upper_bar.prepend(draw_status)
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_tick, first_interval=3.0, persistent=True)


def unregister() -> None:
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    bpy.types.TOPBAR_HT_upper_bar.remove(draw_status)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
