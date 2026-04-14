"""Tests for Maya ASCII DCC canonicalization and registry routing."""
from __future__ import annotations

from pathlib import Path

from maya.canonicalize import canonicalize, extract_semantic_anchors
from zeno_client.dcc_registry import canonicalize_file


def _ma_scene(**volatile: str) -> bytes:
    """Build a minimal .ma with varying volatile fields."""
    fi_application = volatile.get("application", "Maya 2024")
    fi_os = volatile.get("os", "Mac OS X")
    fi_cut = volatile.get("cutIdentifier", "abc123")
    header = volatile.get("header", "//Maya ASCII 2024 scene")
    req = volatile.get("requires", 'requires maya "2024";')
    return f'''{header}
// Created by Maya Version 2024 x64
// Last modified: Mon, Jan 01, 2024 12:00:00 PM
{req}
fileInfo "application" "{fi_application}";
fileInfo "os" "{fi_os}";
fileInfo "cutIdentifier" "{fi_cut}";
fileInfo "customStudio" "keep-me";
createNode transform -n "persp";
'''.encode(
        "utf-8"
    )


def test_fileinfo_volatility_collapses():
    a = canonicalize(_ma_scene(application="Maya 2023", os="Linux", cutIdentifier="x"))
    b = canonicalize(_ma_scene(application="Maya 2025", os="Windows", cutIdentifier="y"))
    assert a == b
    assert b'fileInfo "application" "chimera.canonical"' in b
    assert b'fileInfo "customStudio" "keep-me"' in b


def test_header_requires_and_comments_normalized():
    a = canonicalize(
        _ma_scene(header="//Maya ASCII 7.0 scene", requires='requires maya "2025";')
    )
    assert b"//Maya ASCII scene\n" in a
    assert b'requires maya "chimera.canonical";' in a
    assert b"// Created by Maya\n" in a
    assert b"Last modified" not in a


def test_crlf_normalized():
    raw = "//Maya ASCII 2024 scene\r\nfileInfo \"version\" \"1\";\r\n"
    out = canonicalize(raw.encode("utf-8"))
    assert b"\r" not in out
    assert (
        out.decode()
        == '//Maya ASCII scene\nfileInfo "version" "chimera.canonical";\n'
    )


def test_registry_routes_ma(tmp_path: Path):
    p = tmp_path / "scene.ma"
    p.write_text(
        '//Maya ASCII 2025 scene\nfileInfo "os" "Solaris";\ncreateNode transform;\n',
        encoding="utf-8",
    )
    c = canonicalize_file(p)
    assert c is not None
    assert b"Solaris" not in c
    assert b"chimera.canonical" in c


def test_registry_non_ma_returns_none(tmp_path: Path):
    p = tmp_path / "x.mb"
    p.write_bytes(b"FOR4")
    assert canonicalize_file(p) is None


def test_bom_stripped():
    raw = "\ufeff//Maya ASCII 2024 scene\n".encode("utf-8")
    out = canonicalize(raw)
    assert out.startswith(b"//Maya ASCII scene")


def test_float_quantization_for_dense_numeric_payload(monkeypatch):
    monkeypatch.setenv("CHIMERA_MA_FLOAT_QUANTIZE", "1")
    raw = (
        '//Maya ASCII 2024 scene\n'
        'setAttr ".vt" -type "pointArray" 2 1.49999998 2.124000001 3.0 1.0 4.00000009 5.0000001 6.0 1.0;\n'
    ).encode("utf-8")
    out = canonicalize(raw).decode("utf-8")
    assert "1.50000" in out
    assert "2.12400" in out
    assert "4.00000" in out


def test_plugin_payload_neutralization_allowlisted_type():
    raw = (
        '//Maya ASCII 2024 scene\n'
        'createNode ngst2SkinLayerData -n "ngst2SkinLayerData1";\n'
        'setAttr ".c[0].cdsl" -type "ngst2SkinLayerDataStorage" "ABCDEF012345";\n'
    ).encode("utf-8")
    out = canonicalize(raw).decode("utf-8")
    assert '"chimera.canonical";' in out
    assert "ABCDEF012345" not in out


def test_semantic_anchor_extraction_is_deterministic():
    raw = (
        '//Maya ASCII 2024 scene\n'
        'createNode animCurveTL -n "animCurveTL1";\n'
        'setAttr ".ktv[0:1]" 1 0 24 3;\n'
        'setAttr ".vt" -type "pointArray" 1 1.0 2.0 3.0 1.0;\n'
    ).encode("utf-8")
    canon = canonicalize(raw)
    a1 = extract_semantic_anchors(canon)
    a2 = extract_semantic_anchors(canon)
    assert a1 == a2
    assert len(a1) >= 2
