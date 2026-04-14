from __future__ import annotations

import io
import zipfile

import pytest

from usd.canonicalize import canonicalize, detect_usd_format


def _build_usdz(entries: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as zf:
        for name, payload in entries:
            zf.writestr(name, payload)
    return out.getvalue()


def test_detect_usd_formats():
    assert detect_usd_format(b"#usda 1.0\n") == "usda"
    assert detect_usd_format(b"PXR-USDC\x00\x01payload") == "usdc"
    assert detect_usd_format(b"PK\x03\x04xxxx") == "usdz"
    assert detect_usd_format(b"random-bytes") == "unknown"


def test_usda_and_usdc_passthrough():
    usda = b"#usda 1.0\n(\n)\n"
    usdc = b"PXR-USDC\x00\x00abc"
    assert canonicalize(usda) == usda
    assert canonicalize(usdc) == usdc


def test_usdz_passthrough_when_repack_disabled(monkeypatch):
    monkeypatch.setenv("CHIMERA_USDZ_REPACK", "0")
    raw = _build_usdz(
        [
            ("b.usdc", b"PXR-USDC\x00v2"),
            ("a.usda", b"#usda 1.0\n"),
        ]
    )
    assert canonicalize(raw) == raw


def test_usdz_repack_is_deterministic(monkeypatch):
    monkeypatch.setenv("CHIMERA_USDZ_REPACK", "1")
    raw = _build_usdz(
        [
            ("z/file.txt", b"keep-me"),
            ("a/main.usda", b"#usda 1.0\n"),
            ("b/geo.usdc", b"PXR-USDC\x00abc"),
        ]
    )
    c1 = canonicalize(raw)
    c2 = canonicalize(raw)
    assert c1 == c2


def test_usdz_repack_invalid_zip_raises(monkeypatch):
    monkeypatch.setenv("CHIMERA_USDZ_REPACK", "1")
    with pytest.raises(ValueError, match="Invalid USDZ archive"):
        canonicalize(b"PK\x03\x04not-a-real-zip")
