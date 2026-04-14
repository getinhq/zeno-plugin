"""
Tests for the Blender DCC canonicalization layer.
Validates: decompression, BHEAD pointer zeroing, determinism, and registry routing.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from blender.canonicalize import _BlendABI, _decompress, _parse_abi, _zero_pointers, canonicalize
from zeno_client.dcc_registry import canonicalize_file


# ---------------------------------------------------------------------------
# Helpers to build synthetic .blend data
# ---------------------------------------------------------------------------

def _make_blend_header(ptr_size: int = 8, little_endian: bool = True) -> bytes:
    """Build a minimal 12-byte legacy .blend header."""
    ptr_char = b'-' if ptr_size == 8 else b'_'
    end_char = b'v' if little_endian else b'V'
    return b'BLENDER' + ptr_char + end_char + b'400'


def _make_blend4x_header(little_endian: bool = True) -> bytes:
    """Build a 17-byte Blender 4.x header."""
    end_char = b'v' if little_endian else b'V'
    return b'BLENDER17-01' + end_char + b'0500'


def _make_bhead_block(
    code: bytes,
    payload: bytes,
    old_ptr: int = 0xDEADBEEF_CAFEBABE,
    ptr_size: int = 8,
    little_endian: bool = True,
) -> bytes:
    fmt = '<' if little_endian else '>'
    size_bytes = struct.pack(fmt + 'I', len(payload))
    ptr_bytes = struct.pack(fmt + ('Q' if ptr_size == 8 else 'I'), old_ptr)
    sdna = struct.pack(fmt + 'I', 0)
    count = struct.pack(fmt + 'I', 1)
    return code[:4].ljust(4, b'\x00') + size_bytes + ptr_bytes + sdna + count + payload


def _make_endb(ptr_size: int = 8, little_endian: bool = True) -> bytes:
    fmt = '<' if little_endian else '>'
    return b'ENDB' + struct.pack(fmt + 'I', 0) + b'\x00' * ptr_size + struct.pack(fmt + 'II', 0, 0)


def _synthetic_blend(
    n_blocks: int = 3,
    ptr_size: int = 8,
    little_endian: bool = True,
    volatile_ptr: int = 0xDEADBEEF_CAFEBABE,
) -> bytes:
    """Build a minimal valid .blend file with n_blocks of synthetic geometry data."""
    header = _make_blend_header(ptr_size, little_endian)
    body = b''
    for i in range(n_blocks):
        payload = f"geometry_data_block_{i:03d}".encode() * 16
        body += _make_bhead_block(b'ME\x00\x00', payload, old_ptr=volatile_ptr, ptr_size=ptr_size)
    body += _make_endb(ptr_size, little_endian)
    return header + body


# ---------------------------------------------------------------------------
# Test: decompression
# ---------------------------------------------------------------------------

class TestDecompression:
    def test_raw_blend_passes_through(self):
        raw = _synthetic_blend()
        result = _decompress(raw)
        assert result == raw

    def test_gzip_from_offset_12(self):
        import gzip as _gzip
        header = _make_blend_header()
        body = b"GEOMETRY_DATA" * 100
        compressed = _gzip.compress(body)  # standard gzip, Python 3.9 compat
        data = header + compressed
        result = _decompress(data)
        assert result == header + body

    def test_zstd_whole_file(self):
        pytest.importorskip("zstandard")
        import zstandard as zstd
        raw = _synthetic_blend()
        compressed = zstd.ZstdCompressor().compress(raw)
        result = _decompress(compressed)
        assert result == raw


# ---------------------------------------------------------------------------
# Test: header parsing (ABI detection)
# ---------------------------------------------------------------------------

class TestHeaderParsing:
    def test_legacy_64bit_le(self):
        data = _make_blend_header(ptr_size=8, little_endian=True) + b'\x00' * 100
        abi = _parse_abi(data)
        assert abi.ptr_size == 8
        assert abi.little_endian is True
        assert abi.header_end == 12

    def test_legacy_32bit_be(self):
        data = _make_blend_header(ptr_size=4, little_endian=False) + b'\x00' * 100
        abi = _parse_abi(data)
        assert abi.ptr_size == 4
        assert abi.little_endian is False
        assert abi.header_end == 12

    def test_blender4x_header(self):
        data = _make_blend4x_header(little_endian=True) + b'\x00' * 100
        abi = _parse_abi(data)
        assert abi.ptr_size == 8
        assert abi.little_endian is True
        assert abi.header_end == 17

    def test_invalid_magic_raises(self):
        with pytest.raises(ValueError, match="Not a valid"):
            _parse_abi(b'NOTBLEND' + b'\x00' * 20)


# ---------------------------------------------------------------------------
# Test: BHEAD pointer zeroing
# ---------------------------------------------------------------------------

class TestBheadPointerZeroing:
    def test_pointers_are_zeroed(self):
        volatile_ptr = 0xDEADBEEF_CAFEBABE
        data = _synthetic_blend(n_blocks=4, volatile_ptr=volatile_ptr)
        abi = _parse_abi(data)
        zeroed, count = _zero_pointers(data, abi)
        assert count == 4  # 4 ME blocks zeroed
        # Original data contains the volatile pointer bytes
        ptr_bytes = struct.pack('<Q', volatile_ptr)
        assert ptr_bytes in data
        # Canonical output must NOT contain the volatile pointer
        assert ptr_bytes not in zeroed

    def test_payload_data_unchanged(self):
        data = _synthetic_blend(n_blocks=2)
        abi = _parse_abi(data)
        zeroed, _ = _zero_pointers(data, abi)
        # Geometry payload must survive verbatim
        assert b'geometry_data_block_000' in zeroed
        assert b'geometry_data_block_001' in zeroed

    def test_endb_terminates_correctly(self):
        data = _synthetic_blend(n_blocks=2)
        abi = _parse_abi(data)
        zeroed, _ = _zero_pointers(data, abi)
        assert b'ENDB' in zeroed


# ---------------------------------------------------------------------------
# Test: determinism across saves
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_geometry_different_ptrs_is_identical(self):
        """Two saves of the same geometry with different RAM pointers must produce
        identical canonical output — the core deduplication guarantee."""
        data_session1 = _synthetic_blend(volatile_ptr=0x7FFF_1234_ABCD_0001)
        data_session2 = _synthetic_blend(volatile_ptr=0x7FFF_9999_DEAD_BEEF)
        # Raw bytes are different (different pointers)
        assert data_session1 != data_session2
        # Canonical bytes must be identical
        assert canonicalize(data_session1) == canonicalize(data_session2)

    def test_changed_geometry_produces_different_canonical(self):
        """Different geometry must still produce different canonical output."""
        data_v1 = _synthetic_blend(n_blocks=2, volatile_ptr=0x1111)
        # Build v2 with a different number of blocks (different geometry)
        data_v2 = _synthetic_blend(n_blocks=3, volatile_ptr=0x1111)
        assert canonicalize(data_v1) != canonicalize(data_v2)


# ---------------------------------------------------------------------------
# Test: registry routing
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_blend_extension_routes_to_canonicalizer(self, tmp_path: Path):
        blend_data = _synthetic_blend()
        f = tmp_path / "test.blend"
        f.write_bytes(blend_data)
        result = canonicalize_file(f)
        assert result is not None
        assert result[:7] == b'BLENDER'

    def test_unknown_extension_returns_none(self, tmp_path: Path):
        f = tmp_path / "scene.fbx"
        f.write_bytes(b'FBX_DATA_HERE')
        assert canonicalize_file(f) is None

    def test_dcc_hint_overrides_extension(self, tmp_path: Path):
        # A file named .bin but told it's blender
        blend_data = _synthetic_blend()
        f = tmp_path / "mystery.bin"
        f.write_bytes(blend_data)
        result = canonicalize_file(f, dcc_hint="blender")
        assert result is not None

    def test_maya_ascii_is_canonicalized(self, tmp_path: Path):
        f = tmp_path / "scene.ma"
        f.write_bytes(b'//Maya ASCII 2024 scene\nfileInfo "os" "Linux";\n')
        result = canonicalize_file(f)
        assert result is not None
        assert b"//Maya ASCII scene" in result
        assert b"Linux" not in result
        assert b"chimera.canonical" in result
