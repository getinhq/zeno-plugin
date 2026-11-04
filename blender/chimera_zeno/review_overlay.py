"""Reviewers' drawings over the 3D viewport's camera frame.

Notes store drawings relative to the reviewed picture. A playblast records the camera frame,
so when a viewport looks through the scene camera the camera border is that picture and the
drawings line up with what the reviewer saw. Notes covering the current frame are also listed
under the frame. Outside camera view only a hint is shown.
"""
from __future__ import annotations

import textwrap

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader

from zeno_client.review import bounding_rect, drawing_primitives, drawings_at_frame, notes_at_frame

from . import review_state as state

_handle = None
_SHADOW = (0.0, 0.0, 0.0, 0.6)
_FONT = 0


def _camera_rect(scene, region, rv3d):  # pragma: no cover - Blender runtime
    cam = scene.camera
    if cam is None or cam.type != "CAMERA":
        return None
    corners = [location_3d_to_region_2d(region, rv3d, cam.matrix_world @ v) for v in cam.data.view_frame(scene=scene)]
    if any(c is None for c in corners):
        return None
    rect = bounding_rect([(c.x, c.y) for c in corners])
    return rect if rect[2] > 4 and rect[3] > 4 else None


def _draw_lines(prims, line_shader, fill_shader, region) -> None:  # pragma: no cover - Blender runtime
    viewport = (region.width, region.height)
    for p in prims:
        if p["kind"] == "line" and len(p["points"]) >= 2:
            batch = batch_for_shader(line_shader, "LINE_STRIP", {"pos": p["points"]})
            line_shader.uniform_float("viewportSize", viewport)
            for color, extra in ((_SHADOW, 2.0), (p["color"], 0.0)):
                line_shader.uniform_float("lineWidth", p["width"] + extra)
                line_shader.uniform_float("color", color)
                batch.draw(line_shader)
        elif p["kind"] == "tri":
            batch = batch_for_shader(fill_shader, "TRIS", {"pos": p["points"]})
            fill_shader.uniform_float("color", p["color"])
            batch.draw(fill_shader)


def _text(x: float, y: float, text: str, size: float, color) -> None:  # pragma: no cover - Blender runtime
    blf.size(_FONT, size)
    blf.color(_FONT, *color)
    blf.enable(_FONT, blf.SHADOW)
    blf.shadow(_FONT, 3, 0.0, 0.0, 0.0, 0.85)
    blf.position(_FONT, x, y, 0)
    blf.draw(_FONT, text)
    blf.disable(_FONT, blf.SHADOW)


def _caption(rect, active, ui_scale: float) -> None:  # pragma: no cover - Blender runtime
    """List the notes on this frame under the camera frame, or along its bottom edge when the
    frame fills the view, so the text covers as little of the picture as possible.

    The top-left corner belongs to Blender's own view name and frame text.
    """
    size = 12 * ui_scale
    line_h = size * 1.4
    x = rect[0] + 10 * ui_scale
    chars = max(20, int(rect[2] / (size * 0.55)) - 4)
    lines: list[tuple[str, tuple]] = []
    for n in active[:4]:
        head = f"{n.get('author_name') or 'Note'}"
        if n.get("raised_in"):
            head += f" · {n['raised_in']}"
        body = " ".join(str(n.get("body") or "(drawing)").split())
        color = (1.0, 0.85, 0.3, 1.0) if n.get("status") == "fixed" else (1.0, 1.0, 1.0, 1.0)
        for i, part in enumerate(textwrap.wrap(f"{head}: {body}", chars)[:3]):
            lines.append((part if i == 0 else "   " + part, color))
    if len(active) > 4:
        lines.append((f"+{len(active) - 4} more notes on this frame", (0.8, 0.8, 0.8, 1.0)))
    block = line_h * len(lines)
    below = rect[1] - 6 * ui_scale - line_h
    y = below if below - line_h * (len(lines) - 1) > 4 * ui_scale else rect[1] + 8 * ui_scale + block - line_h
    for text, color in lines:
        _text(x, y, text, size, color)
        y -= line_h


def draw() -> None:  # pragma: no cover - Blender runtime
    context = bpy.context
    scene = context.scene
    region, rv3d = context.region, context.region_data
    if scene is None or rv3d is None or not getattr(scene, "zeno_show_drawings", False):
        return
    task_id = state.current_task(scene)
    if not task_id or state.notes["task_id"] != task_id:
        return
    frame = scene.frame_current
    active = notes_at_frame(state.notes["notes"], frame)
    if not active:
        return
    ui_scale = context.preferences.system.ui_scale
    rect = _camera_rect(scene, region, rv3d) if rv3d.view_perspective == "CAMERA" else None
    if rect is None:
        drawn = len(drawings_at_frame(active, frame))
        hint = f"Zeno: {len(active)} review note(s) on frame {frame}"
        if drawn:
            hint += " · look through the camera (Numpad 0) to see the drawings"
        _text(20 * ui_scale, 60 * ui_scale, hint, 12 * ui_scale, (1.0, 0.85, 0.3, 1.0))
        return
    prims = drawing_primitives(drawings_at_frame(active, frame), rect)
    gpu.state.blend_set("ALPHA")
    try:
        _draw_lines(
            prims,
            gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR"),
            gpu.shader.from_builtin("UNIFORM_COLOR"),
            region,
        )
        for p in prims:
            if p["kind"] == "text" and p["text"]:
                _text(p["pos"][0], p["pos"][1], p["text"], p["size"], p["color"])
        _caption(rect, active, ui_scale)
    finally:
        gpu.state.blend_set("NONE")


def register() -> None:
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(draw, (), "WINDOW", "POST_PIXEL")


def unregister() -> None:
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
