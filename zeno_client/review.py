"""DCC-independent helpers for review submissions and notes."""
from __future__ import annotations

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
