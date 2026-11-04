"""Review state shared by the notes popup, the sidebar panel and the viewport overlay.

- Tasks and notes are fetched on a worker thread so the panel never blocks Blender; a timer
  copies results in and redraws the UI.
- *Pending fixes* are notes the artist ticked as fixed while working. They are saved in the
  .blend (``scene.zeno_pending_fixes``) and sent with the next Submit for Review, so the fix is
  recorded against the revision that contains it.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

import bpy

from .bridge import make_client

AUTO_REFRESH_SECONDS = 120.0

task_items: list[tuple[str, str, str]] = []
# Blender keeps only pointers to the strings an enum callback returns, so the placeholder must
# live as long as the add-on (a fresh list each call shows up as garbage / "matches no enum").
NO_TASK = "NONE"  # enum identifiers cannot be empty; this one means "no task picked"
_NO_TASKS = [(NO_TASK, "No tasks loaded", "")]
notes: dict[str, Any] = {"task_id": "", "notes": [], "error": "", "loading": False, "fetched_at": 0.0}
_worker: threading.Thread | None = None


def launch_task_id() -> str:
    try:
        from zeno_client.launch_context import read_launch_context_from_environ

        ctx = read_launch_context_from_environ()
        return str(getattr(ctx, "task_id", "") or "") if ctx else ""
    except Exception:
        return ""


def _task_rows(tasks: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    items = []
    for t in tasks:
        label = str(t.get("title") or t.get("type") or t.get("id"))
        status = str(t.get("status") or "").replace("_", " ")
        items.append((str(t["id"]), f"{label}  ·  {status}", label))
    return items or [(NO_TASK, "No tasks assigned to you", "")]


def load_task_items() -> list[tuple[str, str, str]]:
    """Blocking fetch, for dialogs that open with a task picker."""
    global task_items
    try:
        task_items = _task_rows(make_client().list_my_tasks())
    except Exception as exc:  # noqa: BLE001
        return [(NO_TASK, "Could not load tasks", str(exc))]
    return task_items


def enum_task_items(self, context):  # pragma: no cover - Blender runtime
    return task_items or _NO_TASKS


def current_task(scene) -> str:  # pragma: no cover - Blender runtime
    """The picked task id, or "" when none is picked."""
    value = str(getattr(scene, "zeno_review_task", "") or "")
    return "" if value == NO_TASK else value


def default_task(scene) -> None:  # pragma: no cover - Blender runtime
    ids = [i[0] for i in task_items if i[0] != NO_TASK]
    wanted = launch_task_id()
    if wanted and wanted in ids:
        scene.zeno_review_task = wanted
    elif ids and current_task(scene) not in ids:
        scene.zeno_review_task = ids[0]


def fetch_notes(task_id: str) -> None:
    """Blocking fetch, used right after a change the user is waiting on."""
    notes.update(task_id=task_id, error="")
    if not task_id:
        notes["notes"] = []
        return
    try:
        notes["notes"] = make_client().list_review_notes(task_id, status="unresolved")
        notes["fetched_at"] = time.monotonic()
    except Exception as exc:  # noqa: BLE001
        notes["error"] = str(exc)


def _redraw() -> None:  # pragma: no cover - Blender runtime
    wm = bpy.context.window_manager
    for window in wm.windows if wm else []:
        for area in window.screen.areas:
            if area.type in ("VIEW_3D", "DOPESHEET_EDITOR", "TIMELINE"):
                area.tag_redraw()


def refresh_async(task_id: str, *, with_tasks: bool = False) -> None:  # pragma: no cover - Blender runtime
    """Fetch notes (and optionally the task list) without blocking; redraws when done."""
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    result: dict[str, Any] = {}
    client = make_client()

    def work() -> None:
        try:
            if with_tasks:
                result["tasks"] = client.list_my_tasks()
            if task_id:
                result["notes"] = client.list_review_notes(task_id, status="unresolved")
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)

    notes.update(loading=True)
    _worker = threading.Thread(target=work, name="zeno-review-notes", daemon=True)
    _worker.start()

    def poll():
        global task_items
        if _worker is not None and _worker.is_alive():
            return 0.25
        if "tasks" in result:
            task_items = _task_rows(result["tasks"])
            scene = bpy.context.scene
            if scene is not None:
                default_task(scene)
        notes.update(task_id=task_id, loading=False, error=result.get("error", ""), fetched_at=time.monotonic())
        if "notes" in result:
            notes["notes"] = result["notes"]
        elif not task_id:
            notes["notes"] = []
        _redraw()
        return None

    bpy.app.timers.register(poll, first_interval=0.25)


def ensure_fresh(task_id: str) -> None:  # pragma: no cover - Blender runtime
    """Called from panel draws: refetch when the task changed or the notes are old."""
    if notes["loading"]:
        return
    stale = time.monotonic() - notes["fetched_at"] > AUTO_REFRESH_SECONDS
    never_loaded = not task_items and notes["fetched_at"] == 0.0
    if notes["task_id"] != task_id or stale or never_loaded:
        # Draw callbacks may not start work that touches the UI; defer to a timer.
        bpy.app.timers.register(lambda: refresh_async(task_id, with_tasks=not task_items), first_interval=0.0)
        notes["loading"] = True


# --- pending fixes ---------------------------------------------------------------------


def parse_pending(raw: str) -> dict[str, list[str]]:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): [str(i) for i in v] for k, v in data.items() if isinstance(v, list)}


def pending_for(scene, task_id: str) -> list[str]:  # pragma: no cover - Blender runtime
    return parse_pending(scene.zeno_pending_fixes).get(task_id, [])


def set_pending(scene, task_id: str, note_ids: list[str]) -> None:  # pragma: no cover - Blender runtime
    data = parse_pending(scene.zeno_pending_fixes)
    if note_ids:
        data[task_id] = sorted(set(note_ids))
    else:
        data.pop(task_id, None)
    scene.zeno_pending_fixes = json.dumps(data, sort_keys=True)


def send_fixes(client, note_ids: list[str], *, item_id: str | None, comment: str) -> tuple[list[str], list[str]]:
    """Mark notes fixed (in ``item_id`` when given). Returns (sent, failed: "id: reason")."""
    sent, failed = [], []
    for note_id in note_ids:
        try:
            client.update_review_note(note_id, status="fixed", comment=comment, fixed_in_item_id=item_id)
            sent.append(note_id)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{note_id}: {exc}")
    return sent, failed


def register() -> None:
    bpy.types.Scene.zeno_review_task = bpy.props.EnumProperty(
        name="Task", items=enum_task_items, options={"SKIP_SAVE"}
    )
    bpy.types.Scene.zeno_pending_fixes = bpy.props.StringProperty(
        name="Pending review fixes",
        description="Notes ticked as fixed; sent with the next Submit for Review",
        default="{}",
        options={"HIDDEN"},
    )
    bpy.types.Scene.zeno_show_drawings = bpy.props.BoolProperty(
        name="Show review drawings",
        description="Draw reviewers' annotations over the camera view on their frames",
        default=True,
    )


def unregister() -> None:
    for name in ("zeno_show_drawings", "zeno_pending_fixes", "zeno_review_task"):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
