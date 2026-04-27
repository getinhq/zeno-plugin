"""Unit tests for :mod:`zeno_ui.dcc_detect`.

We pass explicit ``modules`` / ``env`` maps rather than mutating
``sys.modules`` so the test stays hermetic and parallelisation-safe.
"""
from __future__ import annotations

from zeno_ui import dcc_detect


def test_detect_blender_via_module():
    assert dcc_detect.detect_dcc(modules={"bpy": object()}, env={}) == "blender"


def test_detect_maya_via_module():
    assert dcc_detect.detect_dcc(modules={"maya.cmds": object()}, env={}) == "maya"
    assert dcc_detect.detect_dcc(modules={"maya": object()}, env={}) == "maya"


def test_detect_houdini_nuke_unreal():
    assert dcc_detect.detect_dcc(modules={"hou": object()}, env={}) == "houdini"
    assert dcc_detect.detect_dcc(modules={"nuke": object()}, env={}) == "nuke"
    assert dcc_detect.detect_dcc(modules={"unreal": object()}, env={}) == "unreal"


def test_env_hint_overrides_fallback():
    result = dcc_detect.detect_dcc(modules={}, env={"CHIMERA_DCC": "Katana"})
    assert result == "katana"


def test_returns_na_without_any_signal():
    assert dcc_detect.detect_dcc(modules={}, env={}) in {"N/A", "blender", "maya", "houdini", "nuke", "unreal"}
    # the function may heuristically match the test runner executable name,
    # but for generic pytest runs we always expect 'N/A'
    import sys

    if "blender" not in (sys.executable or "").lower():
        assert dcc_detect.detect_dcc(modules={}, env={}) == "N/A"
