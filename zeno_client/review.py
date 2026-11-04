"""DCC-independent helpers for review submissions and notes."""
from __future__ import annotations

import math
from fractions import Fraction
from typing import Any

MARKER_PREFIX = "zeno: "
PLAYBLAST_MAX_WIDTH = 1920


def fps_string(fps: float, fps_base: float = 1.0) -> str:
    """Exact rate as the API expects it: ``24``, ``25``, ``24000/1001`` (Blender fps/fps_base)."""
    value = Fraction(fps).limit_denominator(1001) / Fraction(fps_base).limit_denominator(1001)
    for ntsc in (Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001)):
        if abs(float(value) - float(ntsc)) < 1e-4:
            return f"{ntsc.numerator}/{ntsc.denominator}"
    value = value.limit_denominator(1001)
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def playblast_percentage(res_x: int, percentage: int) -> int:
    """Resolution percentage that keeps a playblast at most PLAYBLAST_MAX_WIDTH wide."""
    width = res_x * percentage / 100
    if width <= PLAYBLAST_MAX_WIDTH:
        return percentage
    return max(1, int(PLAYBLAST_MAX_WIDTH * 100 / res_x))


def marker_name(note: dict[str, Any], width: int = 48) -> str:
    """Timeline marker label for a review note: ``zeno: ● text``."""
    sign = {"open": "●", "fixed": "◐", "verified": "✓"}.get(str(note.get("status") or ""), "·")
    text = " ".join(str(note.get("body") or "drawing").split())
    if len(text) > width:
        text = text[: width - 1] + "…"
    return f"{MARKER_PREFIX}{sign} {text}"


def note_line(note: dict[str, Any], width: int = 70) -> str:
    """One-line summary for DCC lists: ``1012-1018  [open]  Foot slides (Sam, from v001)``."""
    frame = note.get("frame")
    end = note.get("frame_end")
    where = "" if frame is None else (f"{frame}-{end}" if end not in (None, frame) else str(frame))
    text = " ".join(str(note.get("body") or "(drawing)").split())
    if len(text) > width:
        text = text[: width - 1] + "…"
    who = note.get("author_name") or ""
    origin = f", from {note['raised_in']}" if note.get("raised_in") else ""
    return f"{where:>9}  [{note.get('status') or 'comment'}]  {text}  ({who}{origin})"


# --- review drawings in a DCC viewport -------------------------------------------------
#
# Drawings are stored as ``{"shapes": [{tool, color, width, points, text?}]}`` with points
# normalized to the reviewed picture (0..1, origin top-left) and stroke width as a fraction
# of the picture height (see the dashboard's lib/annotations.js). A DCC draws them over its
# camera frame, which is the picture a playblast records.

DEFAULT_SHAPE_COLOR = "#ff3b30"
DEFAULT_SHAPE_WIDTH = 0.004
_ELLIPSE_SEGMENTS = 48
_CURVE_STEPS = 6

Rect = tuple[float, float, float, float]  # x, y, width, height; y grows upward (DCC pixels)
Point = tuple[float, float]


def notes_at_frame(notes: list[dict[str, Any]], frame: int) -> list[dict[str, Any]]:
    """Notes whose frame (or frame range) covers ``frame``."""
    out = []
    for n in notes or []:
        start = n.get("frame")
        if start is None:
            continue
        end = n.get("frame_end")
        end = start if end is None else end
        if start <= frame <= end:
            out.append(n)
    return out


def drawings_at_frame(notes: list[dict[str, Any]], frame: int) -> list[dict[str, Any]]:
    return [n for n in notes_at_frame(notes, frame) if (n.get("drawing") or {}).get("shapes")]


def adjacent_note_frame(notes: list[dict[str, Any]], frame: int, direction: int) -> int | None:
    """First note frame after (``direction`` > 0) or before ``frame``; None when there is none."""
    frames = sorted({int(n["frame"]) for n in notes or [] if n.get("frame") is not None})
    if direction > 0:
        return next((f for f in frames if f > frame), None)
    return next((f for f in reversed(frames) if f < frame), None)


def hex_color(value: str | None, alpha: float = 1.0) -> tuple[float, float, float, float]:
    text = str(value or DEFAULT_SHAPE_COLOR).lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    try:
        r, g, b = (int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return hex_color(DEFAULT_SHAPE_COLOR, alpha)
    return (r, g, b, alpha)


def to_pixels(rect: Rect, point: Any) -> Point:
    """Normalized picture point (origin top-left) to DCC pixels (origin bottom-left)."""
    x, y, w, h = rect
    return (x + float(point[0]) * w, y + (1.0 - float(point[1])) * h)


def _smooth(pts: list[Point]) -> list[Point]:
    """Same curve as the dashboard pen: quadratic segments through the stroke's midpoints."""
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    start = pts[0]
    for i in range(1, len(pts) - 1):
        ctrl = pts[i]
        end = ((pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2)
        for s in range(1, _CURVE_STEPS + 1):
            t = s / _CURVE_STEPS
            u = 1 - t
            out.append((
                u * u * start[0] + 2 * u * t * ctrl[0] + t * t * end[0],
                u * u * start[1] + 2 * u * t * ctrl[1] + t * t * end[1],
            ))
        start = end
    out.append(pts[-1])
    return out


def shape_primitives(shape: dict[str, Any], rect: Rect) -> list[dict[str, Any]]:
    """Pixel-space primitives for one shape.

    Each primitive is ``{"kind": "line", "points", "width", "color"}`` (a line strip),
    ``{"kind": "tri", "points", "color"}`` or ``{"kind": "text", "pos", "text", "size", "color"}``,
    where ``pos`` is the text baseline's left end.
    """
    pts = [to_pixels(rect, p) for p in shape.get("points") or []]
    if not pts:
        return []
    h = rect[3]
    color = hex_color(shape.get("color"))
    width = max(1.5, float(shape.get("width") or DEFAULT_SHAPE_WIDTH) * h)
    a, b = pts[0], pts[-1]
    tool = shape.get("tool")

    def line(points: list[Point]) -> dict[str, Any]:
        return {"kind": "line", "points": points, "width": width, "color": color}

    if tool == "arrow":
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        head = max(10.0, width * 4)
        shaft_end = (b[0] - math.cos(ang) * head * 0.6, b[1] - math.sin(ang) * head * 0.6)
        tip = [b] + [(b[0] - head * math.cos(ang + d), b[1] - head * math.sin(ang + d)) for d in (-0.45, 0.45)]
        return [line([a, shaft_end]), {"kind": "tri", "points": tip, "color": color}]
    if tool == "rect":
        (x0, y0), (x1, y1) = a, b
        return [line([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])]
    if tool == "ellipse":
        cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        rx, ry = abs(b[0] - a[0]) / 2, abs(b[1] - a[1]) / 2
        ring = [
            (cx + rx * math.cos(2 * math.pi * i / _ELLIPSE_SEGMENTS), cy + ry * math.sin(2 * math.pi * i / _ELLIPSE_SEGMENTS))
            for i in range(_ELLIPSE_SEGMENTS + 1)
        ]
        return [line(ring)]
    if tool == "text":
        size = max(12.0, h * 0.035)
        # The dashboard anchors text at its top-left; DCC text draws from the baseline.
        return [{"kind": "text", "pos": (a[0], a[1] - size), "text": str(shape.get("text") or ""), "size": size, "color": color}]
    if len(pts) == 1:
        return [line([a, (a[0] + 0.01, a[1])])]
    return [line(_smooth(pts))]


def drawing_primitives(notes: list[dict[str, Any]], rect: Rect) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n in notes or []:
        for shape in (n.get("drawing") or {}).get("shapes") or []:
            out.extend(shape_primitives(shape, rect))
    return out


def bounding_rect(points: list[Point]) -> Rect:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def frames_label(note: dict[str, Any]) -> str:
    """``1012``, ``1012–1018`` or ``Whole clip`` for notes without a frame."""
    start, end = note.get("frame"), note.get("frame_end")
    if start is None:
        return "Whole clip"
    return f"{start}–{end}" if end not in (None, start) else str(start)


def sort_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Timeline order; notes without a frame last."""
    return sorted(notes or [], key=lambda n: (n.get("frame") is None, n.get("frame") or 0, n.get("created_at") or ""))


def notes_summary(notes: list[dict[str, Any]], ticked: int = 0) -> str:
    """``2 open · 1 waiting for the reviewer · 1 ticked`` (zero counts left out)."""
    open_n = sum(1 for n in notes or [] if n.get("status") == "open")
    fixed_n = sum(1 for n in notes or [] if n.get("status") == "fixed")
    parts = [f"{open_n} open"]
    if fixed_n:
        parts.append(f"{fixed_n} waiting for the reviewer")
    if ticked:
        parts.append(f"{ticked} ticked")
    return " · ".join(parts)


# --- checks before a review submission -------------------------------------------------


def shot_range(shot: dict[str, Any] | None) -> tuple[int, int, int, int] | None:
    """``(cut_in, cut_out, handle_in, handle_out)`` when the shot has a cut range."""
    if not shot or shot.get("frame_start") is None or shot.get("frame_end") is None:
        return None
    return (int(shot["frame_start"]), int(shot["frame_end"]), int(shot.get("handle_in") or 0), int(shot.get("handle_out") or 0))


def submit_checks(
    scene: dict[str, Any],
    *,
    shot: dict[str, Any] | None = None,
    last: dict[str, Any] | None = None,
    sequences_off: str | None = None,
    as_sequence: bool = False,
) -> list[dict[str, Any]]:
    """What to tell the artist before rendering.

    ``scene``: ``frame_start``, ``frame_end``, ``fps`` (rational string), ``has_camera``.
    Each check is ``{"level": "error" | "warning" | "ok", "text", "fix"?}``; ``fix`` names
    an action the window can offer (``{"action": "use_shot_range", "start", "end", "label"}``).
    Any ``error`` blocks the submission.
    """
    checks: list[dict[str, Any]] = []
    start, end = int(scene["frame_start"]), int(scene["frame_end"])
    if not scene.get("has_camera", True):
        checks.append({"level": "error", "text": "The scene has no camera. The playblast is the camera view."})
    if as_sequence and sequences_off:
        checks.append({"level": "error", "text": f"Image sequences are switched off for this studio ({sequences_off}). Send a movie instead."})

    rng = shot_range(shot)
    code = str((shot or {}).get("shot_code") or "the shot")
    if rng:
        cut_in, cut_out, h_in, h_out = rng
        handles = f", handles {h_in}/{h_out}" if (h_in or h_out) else ""
        with_handles = (cut_in - h_in, cut_out + h_out)
        if (start, end) == (cut_in, cut_out):
            checks.append({"level": "ok", "text": f"Frames {start}–{end} match {code}'s cut{handles}."})
        elif (start, end) == with_handles and (h_in or h_out):
            checks.append({"level": "ok", "text": f"Frames {start}–{end} are {code}'s cut {cut_in}–{cut_out} with handles {h_in}/{h_out}."})
        else:
            fix = {"action": "use_shot_range", "start": cut_in, "end": cut_out, "label": f"Use {cut_in}–{cut_out}"}
            if start > cut_in or end < cut_out:
                text = f"Frames {start}–{end} miss part of {code}'s cut {cut_in}–{cut_out}{handles}."
                checks.append({"level": "warning", "text": text, "fix": fix})
            else:
                text = f"Frames {start}–{end} are wider than {code}'s cut {cut_in}–{cut_out}{handles}."
                checks.append({"level": "warning", "text": text, "fix": fix})
    elif shot is None:
        checks.append({"level": "ok", "text": f"Frames {start}–{end} ({end - start + 1} frames)."})
    else:
        checks.append({"level": "warning", "text": f"{code} has no frame range in Zeno yet, so the range can't be checked."})

    last_fps = (last or {}).get("fps")  # the API sends a float (23.976…); the scene a rational string
    if last_fps and scene.get("fps") and abs(float(last_fps) - float(Fraction(str(scene["fps"])))) > 1e-3:
        checks.append({
            "level": "warning",
            "text": f"{last.get('label') or 'The last revision'} was {fps_string(float(last_fps))} fps; this scene is {scene['fps']} fps. Notes land on the wrong frames if the rate changed by mistake.",
        })
    if last and last.get("frame_start") is not None and int(last["frame_start"]) != start:
        checks.append({
            "level": "warning",
            "text": f"{last.get('label') or 'The last revision'} started at frame {last['frame_start']}; this one starts at {start}. Earlier notes are pinned to scene frames.",
        })
    return checks
