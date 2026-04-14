from __future__ import annotations

from pathlib import Path

from zeno_client.dcc_registry import canonicalize_file


def test_registry_routes_usda(tmp_path: Path):
    p = tmp_path / "scene.usda"
    p.write_bytes(b"#usda 1.0\n")
    out = canonicalize_file(p)
    assert out is not None
    assert out.startswith(b"#usda")


def test_registry_routes_usdc(tmp_path: Path):
    p = tmp_path / "scene.usdc"
    p.write_bytes(b"PXR-USDC\x00\x00")
    out = canonicalize_file(p)
    assert out is not None
    assert out.startswith(b"PXR-USDC")


def test_registry_routes_usdz(tmp_path: Path):
    p = tmp_path / "scene.usdz"
    p.write_bytes(b"PK\x03\x04\x14\x00")
    out = canonicalize_file(p)
    assert out is not None
    assert out[:2] == b"PK"


def test_registry_hint_routes_usd_even_with_unknown_ext(tmp_path: Path):
    p = tmp_path / "scene.bin"
    p.write_bytes(b"#usda 1.0\n")
    out = canonicalize_file(p, dcc_hint="usd")
    assert out is not None
    assert out.startswith(b"#usda")
