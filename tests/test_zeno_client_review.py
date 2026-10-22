from zeno_client.review import MARKER_PREFIX, fps_string, marker_name, note_line, playblast_percentage


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
