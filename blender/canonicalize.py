"""
Chimera Blender DCC Canonicalizer
==================================
Strips Blender's save-time volatility sources before FastCDC chunking:

  1. Transparent decompression — Blender 4.x wraps the entire file in a Zstd frame;
     older versions use GZip from byte 12. We unwrap to raw BLEND bytes first.

  2. BHEAD old_ptr zeroing — every file-block header contains a field holding the
     original heap address from the saving process (volatile due to OS ASLR). We
     replace it with zeros so the same geometry always hashes identically.

Format reference:
  https://wiki.blender.org/wiki/Development/Architecture/File_Format
  Header (legacy/3.x): BLENDER[-_]v### (12 bytes)
  Header (4.x):        BLENDER17-01v#### (17 bytes)
  Per-block:           [code: 4][size: 4][old_ptr: ptr_size][sdna_idx: 4][count: 4][data: size]
"""
from __future__ import annotations

import gzip
import io
import struct
from pathlib import Path
from typing import NamedTuple

try:
    import zstandard as zstd_mod
except ImportError:  # pragma: no cover
    zstd_mod = None  # type: ignore


class _BlendABI(NamedTuple):
    ptr_size: int       # 4 or 8 bytes
    little_endian: bool
    header_end: int     # byte offset where first block begins


# ---------------------------------------------------------------------------
# Decompression
# ---------------------------------------------------------------------------

def _decompress(data: bytes) -> bytes:
    """
    Strip any outer Zstd or GZip wrapper transparently.

    Blender 4.x: entire file is a single Zstd frame (starts with 0x28 B5 2F FD).
    Blender 3.x: bytes 0-11 are header, bytes 12+ are a Zstd or GZip frame.
    Blender 2.x: bytes 0-11 are header, bytes 12+ are GZip.
    Uncompressed: returned as-is.

    IMPORTANT: Blender .blend files are Zstd *multi-frame* streams. A naive
    ZstdDecompressor().decompress() call stops at the first frame boundary
    and silently truncates the output to ~66 KB instead of the full ~32 MB.
    We use stream_reader with read_across_frames=True to consume all frames.
    """
    # Blender 4.x — whole file is Zstd (multi-frame)
    if data[:4] == b'\x28\xb5\x2f\xfd':
        if zstd_mod is None:
            raise RuntimeError("zstandard package required to decompress Blender 4.x files")
        dctx = zstd_mod.ZstdDecompressor()
        with dctx.stream_reader(io.BytesIO(data), read_across_frames=True) as reader:
            return reader.read()

    if len(data) < 13:
        return data

    body = data[12:]

    # Blender 3.x — body starts with Zstd (multi-frame)
    if body[:4] == b'\x28\xb5\x2f\xfd':
        if zstd_mod is None:
            raise RuntimeError("zstandard package required to decompress Blender 3.x files")
        dctx = zstd_mod.ZstdDecompressor()
        with dctx.stream_reader(io.BytesIO(body), read_across_frames=True) as reader:
            return data[:12] + reader.read()

    # Legacy — body starts with GZip
    if body[:2] == b'\x1f\x8b':
        with gzip.open(io.BytesIO(body)) as gz:
            return data[:12] + gz.read()

    return data  # already raw


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

def _parse_abi(data: bytes) -> _BlendABI:
    if len(data) < 12 or data[:7] != b'BLENDER':
        raise ValueError("Not a valid .blend file (bad magic or too short)")

    # Blender 4.x: "BLENDER17-01v####" → 17-byte header, always 64-bit LE
    if len(data) >= 17 and data[7:9] == b'17':
        little_endian = data[12] == ord('v')
        return _BlendABI(ptr_size=8, little_endian=little_endian, header_end=17)

    # Legacy: byte 7 = '-' (64-bit) or '_' (32-bit), byte 8 = 'v' (LE) or 'V' (BE)
    ptr_size = 8 if data[7] == ord('-') else 4
    little_endian = data[8] == ord('v')
    return _BlendABI(ptr_size=ptr_size, little_endian=little_endian, header_end=12)


# ---------------------------------------------------------------------------
# BHEAD old_ptr zeroing
# ---------------------------------------------------------------------------

def _zero_pointers(data: bytes, abi: _BlendABI) -> tuple[bytes, int]:
    """
    Walk every BHEAD block and replace old_ptr with zeros.

    Block layout (all fields are abi.little_endian):
      code      [4 bytes]
      size      [4 bytes]   ← byte count of the data payload
      old_ptr   [ptr_size]  ← ASLR-volatile — we zero this
      sdna_idx  [4 bytes]
      count     [4 bytes]
      data      [size bytes]
    """
    out = bytearray(data[:abi.header_end])
    offset = abi.header_end
    zeroed = 0
    fmt_u32 = '<I' if abi.little_endian else '>I'
    fixed_hdr = 8 + abi.ptr_size + 8  # code + size + ptr + sdna + count

    while offset + fixed_hdr <= len(data):
        code = data[offset:offset + 4]

        if code == b'ENDB':
            out.extend(data[offset:])
            break

        (block_data_size,) = struct.unpack_from(fmt_u32, data, offset + 4)
        block_total = fixed_hdr + block_data_size

        if offset + block_total > len(data):
            # Truncated block — passthrough remainder as-is
            out.extend(data[offset:])
            break

        # code + size (unchanged)
        out.extend(data[offset:offset + 8])
        # old_ptr → all zeros
        out.extend(b'\x00' * abi.ptr_size)
        # sdna_idx + count + payload (unchanged)
        out.extend(data[offset + 8 + abi.ptr_size:offset + block_total])

        offset += block_total
        zeroed += 1

    return bytes(out), zeroed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def canonicalize(raw_data: bytes) -> bytes:
    """
    Return a canonical version of a .blend file's bytes suitable for
    content-defined chunking. The canonical form:
      - is fully decompressed (no Zstd/GZip wrapper)
      - has all BHEAD old_ptr fields zeroed out

    The returned bytes can be opened by Blender directly (it reads
    uncompressed .blend files natively).

    Args:
        raw_data: raw bytes as read from disk (may be compressed)

    Returns:
        Canonical bytes (deterministic across OS saves and machines)

    Raises:
        ValueError: if the data does not look like a .blend file
        RuntimeError: if a required decompression library is missing
    """
    decompressed = _decompress(raw_data)
    abi = _parse_abi(decompressed)
    canonical, zeroed = _zero_pointers(decompressed, abi)
    return canonical


def canonicalize_path(path: str | Path) -> bytes:
    """Convenience wrapper that reads a file and canonicalizes it."""
    return canonicalize(Path(path).read_bytes())
