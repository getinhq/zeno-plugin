import json

import httpx
import pytest

from zeno_client.client import ZenoClient
from zeno_client.review import (
    MARKER_PREFIX,
    adjacent_note_frame,
    bounding_rect,
    drawing_primitives,
    drawings_at_frame,
    fps_string,
    hex_color,
    marker_name,
    note_line,
    notes_at_frame,
    playblast_percentage,
    shape_primitives,
    to_pixels,
)


def test_fps_string_exact_rates():
    assert fps_string(24) == "24"
    assert fps_string(25, 1.0) == "25"
    assert fps_string(24, 1.001) == "24000/1001"
    assert fps_string(30, 1.001) == "30000/1001"
    assert fps_string(60, 1.001) == "60000/1001"
    assert fps_string(12, 1.0) == "12"
    assert fps_string(48, 2.0) == "24"


def test_playblast_width_cap():
    assert playblast_percentage(1920, 100) == 100
    assert playblast_percentage(3840, 100) == 50
    assert playblast_percentage(3000, 50) == 50  # 1500 px wide: already small enough
    assert playblast_percentage(4096, 50) == 46
    assert playblast_percentage(4096, 100) == 46


def test_marker_and_line():
    n = {"frame": 1012, "frame_end": 1018, "status": "open", "body": "Foot  slides\nhere", "author_name": "Sam", "raised_in": "v001"}
    assert marker_name(n) == f"{MARKER_PREFIX}● Foot slides here"
    assert marker_name({"status": "fixed", "body": "x" * 80}).endswith("…")
    assert note_line(n).strip().startswith("1012-1018  [open]  Foot slides here  (Sam, from v001)")


# --- viewport drawings ------------------------------------------------------------------

RECT = (100.0, 50.0, 400.0, 200.0)  # camera frame in region pixels, origin bottom-left


def test_to_pixels_flips_y():
    assert to_pixels(RECT, (0, 0)) == (100.0, 250.0)  # picture top-left
    assert to_pixels(RECT, (1, 1)) == (500.0, 50.0)  # picture bottom-right
    assert to_pixels(RECT, (0.5, 0.5)) == (300.0, 150.0)


def test_notes_at_frame_ranges_and_drawings():
    notes = [
        {"id": "a", "frame": 10},
        {"id": "b", "frame": 12, "frame_end": 20, "drawing": {"shapes": [{"tool": "pen", "points": [[0, 0]]}]}},
        {"id": "c", "frame": None},
        {"id": "d", "frame": 15, "drawing": {"shapes": []}},
    ]
    assert [n["id"] for n in notes_at_frame(notes, 15)] == ["b", "d"]
    assert [n["id"] for n in notes_at_frame(notes, 10)] == ["a"]
    assert [n["id"] for n in drawings_at_frame(notes, 15)] == ["b"]
    assert notes_at_frame(notes, 21) == []


def test_adjacent_note_frame():
    notes = [{"frame": 30}, {"frame": 10}, {"frame": 10}, {"frame": None}]
    assert adjacent_note_frame(notes, 0, 1) == 10
    assert adjacent_note_frame(notes, 10, 1) == 30
    assert adjacent_note_frame(notes, 30, 1) is None
    assert adjacent_note_frame(notes, 30, -1) == 10
    assert adjacent_note_frame(notes, 10, -1) is None


def test_hex_color():
    assert hex_color("#ffffff") == (1.0, 1.0, 1.0, 1.0)
    assert hex_color("#f00", 0.5) == (1.0, 0.0, 0.0, 0.5)
    assert hex_color("nonsense") == hex_color("#ff3b30")
    assert hex_color(None) == hex_color("#ff3b30")


def test_rect_and_width_scale_with_picture_height():
    [p] = shape_primitives({"tool": "rect", "color": "#0a84ff", "width": 0.01, "points": [[0, 0], [0.5, 0.5]]}, RECT)
    assert p["kind"] == "line"
    assert p["points"][0] == p["points"][-1]  # closed
    assert {pt for pt in p["points"]} == {(100.0, 250.0), (300.0, 250.0), (300.0, 150.0), (100.0, 150.0)}
    assert p["width"] == pytest.approx(2.0)  # 1% of 200 px
    assert shape_primitives({"tool": "rect", "width": 0.0001, "points": [[0, 0], [1, 1]]}, RECT)[0]["width"] == 1.5


def test_arrow_has_shaft_and_head_at_end():
    shaft, head = shape_primitives({"tool": "arrow", "points": [[0, 0.5], [1, 0.5]]}, RECT)
    assert shaft["kind"] == "line" and head["kind"] == "tri"
    tip = head["points"][0]
    assert tip == (500.0, 150.0)
    assert shaft["points"][0] == (100.0, 150.0)
    assert shaft["points"][1][0] < 500.0  # shaft stops short of the tip
    assert all(x <= 500.0 for x, _ in head["points"])


def test_ellipse_inscribed_in_box():
    [p] = shape_primitives({"tool": "ellipse", "points": [[0, 0], [1, 1]]}, RECT)
    xs = [x for x, _ in p["points"]]
    ys = [y for _, y in p["points"]]
    assert min(xs) == pytest.approx(100.0) and max(xs) == pytest.approx(500.0)
    assert min(ys) == pytest.approx(50.0) and max(ys) == pytest.approx(250.0)
    assert p["points"][0] == pytest.approx(p["points"][-1])


def test_text_anchored_from_top_left():
    [p] = shape_primitives({"tool": "text", "text": "too dark", "points": [[0, 0]]}, RECT)
    assert p["kind"] == "text" and p["text"] == "too dark"
    assert p["size"] == 12.0  # max(12, 3.5% of 200)
    assert p["pos"] == (100.0, 250.0 - 12.0)


def test_pen_is_smoothed_and_keeps_endpoints():
    [p] = shape_primitives({"tool": "pen", "points": [[0, 0], [0.5, 1], [1, 0]]}, RECT)
    assert p["points"][0] == (100.0, 250.0) and p["points"][-1] == (500.0, 250.0)
    assert len(p["points"]) > 3
    [dot] = shape_primitives({"points": [[0.5, 0.5]]}, RECT)  # a click: tiny visible stroke
    assert len(dot["points"]) == 2


def test_drawing_primitives_skips_empty():
    notes = [{"drawing": None}, {"drawing": {"shapes": [{"tool": "rect", "points": [[0, 0], [1, 1]]}, {"points": []}]}}]
    assert len(drawing_primitives(notes, RECT)) == 1


def test_bounding_rect():
    assert bounding_rect([(10, 20), (110, 20), (110, 70), (10, 70)]) == (10, 20, 100, 50)


def test_update_review_note_sends_fixed_in_revision():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"id": "n1", "notes": []})

    c = ZenoClient(base_url="http://api", transport=httpx.MockTransport(handler))
    c.update_review_note("n1", status="fixed", comment="Fixed in v004", fixed_in_item_id="item4")
    assert seen["path"] == "/api/v1/reviews/notes/n1"
    assert seen["body"] == {"status": "fixed", "comment": "Fixed in v004", "fixed_in_item_id": "item4"}
    c.update_review_note("n1", status="fixed")
    assert seen["body"] == {"status": "fixed"}
