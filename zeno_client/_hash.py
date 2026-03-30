from __future__ import annotations

from pathlib import Path

from blake3 import blake3


def compute_blake3(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    p = Path(path)
    h = blake3()
    with p.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def compute_content_hash(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Canonical file hash helper (BLAKE3)."""
    return compute_blake3(path, chunk_size=chunk_size)


def compute_sha256(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """
    Backward-compatible alias for callers still importing compute_sha256.

    Chimera hard-cutover uses BLAKE3 content identity; this function intentionally
    returns a BLAKE3 hex digest.
    """
    return compute_blake3(path, chunk_size=chunk_size)

