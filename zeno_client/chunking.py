from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


@dataclass(frozen=True)
class ChunkingConfig:
    algo: str = "fastcdc"
    avg: int = 1024 * 1024
    min: int = 256 * 1024
    max: int = 4 * 1024 * 1024


@dataclass(frozen=True)
class Chunk:
    offset: int
    size: int
    sha256: str


# Gear hash table (deterministic). 256 64-bit constants derived from SHA-256(i).
_GEAR_TABLE = [int(hashlib.sha256(bytes([i])).hexdigest()[:16], 16) for i in range(256)]


def _mask_for_avg(avg: int) -> int:
    # avg ~ 2^n => mask = (1<<n)-1
    # clamp n to [8..24] ~ [256B..16MiB]
    n = max(8, min(24, int(round(avg).bit_length() - 1)))
    return (1 << n) - 1


def iter_chunks(path: str | Path, cfg: ChunkingConfig | None = None) -> Iterator[Chunk]:
    """
    Content-defined chunking (FastCDC-like gear hash).

    - Reads the file sequentially, decides cut points using a rolling gear hash.
    - Returns chunk metadata with sha256 over chunk bytes.
    """
    cfg = cfg or ChunkingConfig()
    p = Path(path)
    size_total = p.stat().st_size
    if size_total == 0:
        yield Chunk(offset=0, size=0, sha256=hashlib.sha256(b"").hexdigest())
        return

    # FastCDC style: early region uses stricter mask (rarer cuts), later region uses looser mask.
    # Derive masks from avg size.
    base_mask = _mask_for_avg(cfg.avg)
    mask_small = (base_mask << 1) | 1
    mask_large = base_mask

    offset = 0
    with p.open("rb") as f:
        while offset < size_total:
            remaining = size_total - offset
            target_max = min(cfg.max, remaining)

            # Read up to max chunk into memory. For large DCC files, this is bounded by cfg.max (default 4MiB).
            buf = f.read(target_max)
            if not buf:
                break

            cut = _find_cut(buf, cfg.min, cfg.max, mask_small, mask_large)
            chunk_bytes = buf[:cut]

            h = hashlib.sha256(chunk_bytes).hexdigest()
            yield Chunk(offset=offset, size=cut, sha256=h)

            # Seek back the unread tail (if any)
            tail = len(buf) - cut
            if tail:
                f.seek(-tail, 1)
            offset += cut


def _find_cut(
    buf: bytes,
    min_size: int,
    max_size: int,
    mask_small: int,
    mask_large: int,
) -> int:
    """
    Pick a cut point in buf. Returns cut length (1..len(buf)).

    This is a simplified FastCDC variant:
    - no cut before min_size
    - between min and mid: use stricter mask_small
    - between mid and max: use mask_large
    - if no cut found: cut at len(buf)
    """
    n = len(buf)
    if n <= min_size:
        return n

    mid = min(n, (min_size + max_size) // 2)
    h = 0

    # Phase 1: min..mid with mask_small
    for i in range(min_size, mid):
        h = ((h << 1) + _GEAR_TABLE[buf[i]]) & 0xFFFFFFFFFFFFFFFF
        if (h & mask_small) == 0:
            return i + 1

    # Phase 2: mid..n with mask_large
    for i in range(mid, n):
        h = ((h << 1) + _GEAR_TABLE[buf[i]]) & 0xFFFFFFFFFFFFFFFF
        if (h & mask_large) == 0:
            return i + 1

    return n


def chunk_file(path: str | Path, cfg: ChunkingConfig | None = None) -> list[Chunk]:
    return list(iter_chunks(path, cfg=cfg))

