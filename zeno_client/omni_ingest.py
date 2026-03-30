from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blake3 import blake3

from .chunking import Chunk, ChunkingConfig, iter_chunks, iter_chunks_in_range
from .entropy_segment import EntropyConfig, scan_entropy_segments
from .manifest import build_manifest_v3, manifest_blake3

try:
    import zstandard as zstd
except Exception:  # pragma: no cover
    zstd = None


@dataclass(frozen=True)
class OmniIngestResult:
    manifest_id: str
    manifest_bytes: bytes
    whole_file_blake3: str
    chunks: list[Chunk]
    uploaded_chunks: int
    uploaded_aux_blobs: int
    segments: list[dict[str, Any]]
    dcc_canonical: bool = False  # True when chunking was done on canonical bytes


def _read_range(path: str | Path, start: int, end: int) -> bytes:
    p = Path(path)
    with p.open("rb") as f:
        f.seek(start)
        return f.read(max(0, end - start))


def _scan_entropy_bytes(data: bytes, cfg: "EntropyConfig | None" = None) -> list:
    """Run entropy segmentation on an in-memory buffer (used for canonical bytes)."""
    import tempfile, os
    from .entropy_segment import scan_entropy_segments
    # Write to a temp file so we can reuse the existing file-based scanner
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        return scan_entropy_segments(tmp_path, cfg)
    finally:
        os.unlink(tmp_path)


def _iter_chunks_from_bytes(data: bytes, base_offset: int, cfg: "ChunkingConfig") -> list:
    """Chunk an in-memory byte buffer, returning Chunk objects with absolute offsets."""
    import tempfile, os
    from .chunking import iter_chunks_in_range
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        return list(iter_chunks_in_range(tmp_path, start=0, length=len(data), cfg=cfg,
                                         base_offset=base_offset))
    except TypeError:
        # Fallback: iter_chunks_in_range without base_offset — patch offsets manually
        chunks = list(iter_chunks_in_range(tmp_path, start=0, length=len(data), cfg=cfg))
        from .chunking import Chunk
        return [Chunk(offset=c.offset + base_offset, size=c.size, content_hash=c.content_hash) for c in chunks]
    finally:
        os.unlink(tmp_path)


def _zstd_training_samples(parent_bytes: bytes, *, max_chunk: int = 256 * 1024) -> list[bytes]:
    """COVER / fastCover needs multiple samples; one huge buffer or only two large chunks can fail."""
    if len(parent_bytes) < 2:
        return []
    n = len(parent_bytes)
    target_chunks = max(8, min(256, n // 4096))
    step = max(1, min(max_chunk, n // target_chunks))
    samples = [parent_bytes[i : i + step] for i in range(0, n, step)]
    if len(samples) < 2:
        mid = n // 2
        if mid < 1:
            return [parent_bytes, parent_bytes]
        return [parent_bytes[:mid], parent_bytes[mid:]]
    return samples


def _high_entropy_patch(
    *,
    new_bytes: bytes,
    parent_bytes: bytes | None,
) -> tuple[bytes, bytes] | None:
    if zstd is None or parent_bytes is None:
        return None
    if len(parent_bytes) == 0 or len(new_bytes) == 0:
        return None
    dict_size = min(131072, max(1024, len(parent_bytes)))
    samples = _zstd_training_samples(parent_bytes)
    if not samples:
        return None
    try:
        dict_obj = zstd.train_dictionary(dict_size, samples)
    except Exception:
        return None
    compressor = zstd.ZstdCompressor(level=19, dict_data=dict_obj)
    patch_blob = compressor.compress(new_bytes)
    return dict_obj.as_bytes(), patch_blob


def ingest_omni_file(
    *,
    client,
    path: str | Path,
    filename: str,
    chunking: ChunkingConfig,
    entropy_cfg: EntropyConfig | None = None,
    parent_content_id: str | None = None,
    parent_local_path: str | Path | None = None,
    canonical_bytes: bytes | None = None,
    dcc: str | None = None,
) -> OmniIngestResult:
    """
    Ingest a file into the Chimera CAS store.

    When `canonical_bytes` is supplied (e.g. from a DCC canonicalizer such as
    the Blender BHEAD-zeroing pre-processor), chunking and deduplication are
    performed against those bytes rather than the raw file, yielding significantly
    higher hit rates on DCC asset versions.

    The blobs stored in the CAS are the canonical bytes so that restore always
    produces a file Blender (or any DCC) can open directly.
    """
    p = Path(path)
    # Hash the original on-disk file — kept as provenance/audit trail
    whole_bytes = p.read_bytes()
    source_blake3 = blake3(whole_bytes).hexdigest()

    # Decide which byte buffer to chunk against
    chunk_source: bytes = canonical_bytes if canonical_bytes is not None else whole_bytes
    dcc_canonical = canonical_bytes is not None

    # The manifest's whole_file_blake3 always refers to what is stored and rebuilt.
    # In canonical mode that is the canonical bytes; otherwise the raw on-disk bytes.
    whole_file_blake3 = blake3(chunk_source).hexdigest()

    # For entropy segmentation we always inspect the chunk_source
    # (write to a temp-like in-memory path adapter if needed)
    # We use a simple BytesIO scan when canonical bytes differ from the file.
    if dcc_canonical:
        segments = _scan_entropy_bytes(chunk_source, entropy_cfg)
    else:
        segments = scan_entropy_segments(p, entropy_cfg)

    parent_bytes_all: bytes | None = None
    if parent_local_path:
        pp = Path(parent_local_path)
        if pp.exists():
            # Also canonicalize the parent if we're in canonical mode
            if dcc_canonical:
                try:
                    from .dcc_registry import canonicalize_file
                    _cb = canonicalize_file(pp)
                    parent_bytes_all = _cb if _cb is not None else pp.read_bytes()
                except Exception:
                    parent_bytes_all = pp.read_bytes()
            else:
                parent_bytes_all = pp.read_bytes()

    uploaded_chunks = 0
    uploaded_aux_blobs = 0
    chunks: list[Chunk] = []
    manifest_segments: list[dict[str, Any]] = []

    for seg in segments:
        if seg.mode in ("low", "mid"):
            # Chunk the canonical/raw buffer for this segment
            seg_bytes = chunk_source[seg.start:seg.end]
            offset = seg.start
            for ch in _iter_chunks_from_bytes(seg_bytes, offset, chunking):
                chunks.append(ch)
                if not client.blob_exists(ch.content_hash):
                    body = chunk_source[ch.offset:ch.offset + ch.size]
                    client.upload_blob_bytes(body, ch.content_hash)
                    uploaded_chunks += 1
                manifest_segments.append({"kind": "raw_chunk", "hash": ch.content_hash, "size": int(ch.size)})
            continue

        # high entropy branch: attempt zstd dict patch, else fallback to chunking
        new_bytes = chunk_source[seg.start:seg.end]
        parent_segment = None
        if parent_bytes_all is not None:
            plen = len(parent_bytes_all)
            if seg.start < plen:
                parent_end = min(seg.end, plen)
                parent_segment = parent_bytes_all[seg.start:parent_end]
        patch_payload = _high_entropy_patch(new_bytes=new_bytes, parent_bytes=parent_segment)
        if patch_payload is None:
            seg_bytes = chunk_source[seg.start:seg.end]
            for ch in _iter_chunks_from_bytes(seg_bytes, seg.start, chunking):
                chunks.append(ch)
                if not client.blob_exists(ch.content_hash):
                    body = chunk_source[ch.offset:ch.offset + ch.size]
                    client.upload_blob_bytes(body, ch.content_hash)
                    uploaded_chunks += 1
                manifest_segments.append({"kind": "raw_chunk", "hash": ch.content_hash, "size": int(ch.size)})
            continue

        dict_blob, patch_blob = patch_payload
        dict_hash = blake3(dict_blob).hexdigest()
        patch_hash = blake3(patch_blob).hexdigest()
        if not client.blob_exists(dict_hash):
            client.upload_blob_bytes(dict_blob, dict_hash)
            uploaded_aux_blobs += 1
        if not client.blob_exists(patch_hash):
            client.upload_blob_bytes(patch_blob, patch_hash)
            uploaded_aux_blobs += 1

        manifest_segments.append(
            {
                "kind": "zstd_dict_patch",
                "parent_content_id": (parent_content_id or "").strip().lower(),
                "range_start": int(seg.start),
                "range_end": int(seg.end),
                "dict_hash": dict_hash,
                "patch_hash": patch_hash,
                "patch_size": len(patch_blob),
                "uncompressed_size": len(new_bytes),
            }
        )

    manifest_bytes = build_manifest_v3(
        filename=filename,
        size_bytes=len(chunk_source),          # canonical size when DCC active, else on-disk size
        whole_file_blake3=whole_file_blake3,   # hash of what is actually stored & rebuilt
        chunking=chunking,
        segments=manifest_segments,
        omni={
            "entropy_thresholds": {
                "low": (entropy_cfg.low_threshold if entropy_cfg else 4.9),
                "high": (entropy_cfg.high_threshold if entropy_cfg else 7.2),
            },
            "window_size": (entropy_cfg.window_size if entropy_cfg else 16 * 1024),
            "engine_version": "omni-v2",
            "dcc_canonical": dcc_canonical,
            "dcc": (dcc or ""),
            # Provenance: original compressed on-disk file identity
            "source_blake3": source_blake3,
            "source_size_bytes": p.stat().st_size,
        },
    )
    mid = manifest_blake3(manifest_bytes)
    if not client.blob_exists(mid):
        client.upload_blob_bytes(manifest_bytes, mid)

    return OmniIngestResult(
        manifest_id=mid,
        manifest_bytes=manifest_bytes,
        whole_file_blake3=whole_file_blake3,
        chunks=chunks,
        uploaded_chunks=uploaded_chunks,
        uploaded_aux_blobs=uploaded_aux_blobs,
        segments=manifest_segments,
        dcc_canonical=dcc_canonical,
    )


def materialize_from_manifest_v3(
    *,
    manifest: dict[str, Any],
    client,
    out_path: str | Path,
    parent_bytes: bytes | None = None,
) -> Path:
    if str(manifest.get("schema") or "").strip().lower() != "chimera.manifest.v3":
        raise ValueError("manifest must be chimera.manifest.v3")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as w:
        for seg in manifest.get("segments") or []:
            kind = str(seg.get("kind") or "").strip().lower()
            if kind == "raw_chunk":
                h = str(seg.get("hash") or "").strip().lower()
                w.write(client.get_blob_bytes(h))
                continue
            if kind == "zstd_dict_patch":
                if zstd is None:
                    raise RuntimeError("zstandard is required for zstd_dict_patch materialization")
                dict_hash = str(seg.get("dict_hash") or "").strip().lower()
                patch_hash = str(seg.get("patch_hash") or "").strip().lower()
                uncompressed_size = int(seg.get("uncompressed_size") or 0)
                dbytes = client.get_blob_bytes(dict_hash)
                pbytes = client.get_blob_bytes(patch_hash)
                zd = zstd.ZstdCompressionDict(dbytes)
                dctx = zstd.ZstdDecompressor(dict_data=zd)
                data = dctx.decompress(pbytes, max_output_size=max(1, uncompressed_size))
                w.write(data)
                continue
            raise ValueError(f"unsupported segment kind: {kind}")
    return out
