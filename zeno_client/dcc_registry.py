"""
zeno_client DCC registry
========================
Routes a file path to the appropriate DCC canonicalizer based on extension.
Returning None means "no canonicalization supported" — callers fall back to
the existing raw-bytes pipeline with zero behaviour change.

Adding a new DCC (Phase 2+):
  1. Create {dcc}/canonicalize.py with a `canonicalize(raw_bytes) -> bytes` function
  2. Add the extension mapping below
"""
from __future__ import annotations

from pathlib import Path


def canonicalize_file(path: str | Path, dcc_hint: str | None = None) -> bytes | None:
    """
    Return canonical bytes for the given file, or None if the file type
    has no registered DCC canonicalizer.

    Args:
        path:      Path to the source file on disk
        dcc_hint:  Optional explicit DCC name (e.g. "blender"). When omitted,
                   the extension is used for routing.

    Returns:
        Canonical bytes ready for FastCDC chunking, or None to use raw bytes.
    """
    p = Path(path)
    ext = p.suffix.lower()
    hint = (dcc_hint or "").lower()

    # ── Blender ──────────────────────────────────────────────────────────────
    if ext == ".blend" or hint == "blender":
        from blender.canonicalize import canonicalize
        return canonicalize(p.read_bytes())

    # ── Maya (Phase 2) ───────────────────────────────────────────────────────
    # if ext in (".ma", ".mb") or hint == "maya":
    #     from maya.canonicalize import canonicalize
    #     return canonicalize(p.read_bytes())

    # ── Houdini (Phase 3) ────────────────────────────────────────────────────
    # if ext in (".hip", ".hipnc") or hint == "houdini":
    #     from houdini.canonicalize import canonicalize
    #     return canonicalize(p.read_bytes())

    # ── Unreal Engine (Phase 4) ──────────────────────────────────────────────
    # if ext in (".uasset", ".umap") or hint == "unreal":
    #     from unreal.canonicalize import canonicalize
    #     return canonicalize(p.read_bytes())

    return None  # Unknown / unsupported — caller uses raw bytes
