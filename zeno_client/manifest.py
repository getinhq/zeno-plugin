from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, List, Literal, Optional, Union

from blake3 import blake3

from .chunking import Chunk, ChunkingConfig


MANIFEST_SCHEMA_V1 = "chimera.manifest.v1"
MANIFEST_SCHEMA_V2 = "chimera.manifest.v2"
MANIFEST_SCHEMA_V3 = "chimera.manifest.v3"


@dataclass(frozen=True)
class ManifestChunk:
    hash: str
    size: int


@dataclass(frozen=True)
class ManifestSegmentRawChunk:
    kind: Literal["raw_chunk"]
    hash: str
    size: int


@dataclass(frozen=True)
class ManifestSegmentZstdDictPatch:
    kind: Literal["zstd_dict_patch"]
    parent_content_id: str
    range_start: int
    range_end: int
    dict_hash: str
    patch_hash: str
    patch_size: int
    uncompressed_size: int


ManifestSegment = Union[ManifestSegmentRawChunk, ManifestSegmentZstdDictPatch]


@dataclass(frozen=True)
class ManifestV1:
    schema: str
    filename: str
    size_bytes: int
    whole_file_blake3: str
    chunking: ChunkingConfig
    chunks: List[ManifestChunk]
    segments: List[ManifestSegment] | None = None
    hash_algo: str = "blake3"

    @property
    def whole_file_sha256(self) -> str:
        """Backward-compatible alias name for legacy call sites."""
        return self.whole_file_blake3


def build_manifest_v1(
    *,
    filename: str,
    size_bytes: int,
    whole_file_blake3: str | None = None,
    whole_file_sha256: str | None = None,
    chunking: ChunkingConfig,
    chunks: Iterable[Chunk],
) -> bytes:
    whole = str(whole_file_blake3 or whole_file_sha256 or "").strip().lower()
    if not whole:
        raise ValueError("build_manifest_v1 requires whole_file_blake3 (or legacy whole_file_sha256)")
    payload: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA_V1,
        "hash_algo": "blake3",
        "filename": filename,
        "size_bytes": int(size_bytes),
        "whole_file_blake3": whole,
        # Legacy field retained for backward compatibility with v1 readers.
        "whole_file_sha256": whole,
        "chunking": {
            "algo": chunking.algo,
            "avg": int(chunking.avg),
            "min": int(chunking.min),
            "max": int(chunking.max),
        },
        "chunks": [{"hash": c.content_hash, "size": int(c.size)} for c in chunks],
    }
    # Deterministic encoding so content ID is stable
    s = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return s.encode("utf-8")


def build_manifest_v2(
    *,
    filename: str,
    size_bytes: int,
    whole_file_blake3: str,
    chunking: ChunkingConfig,
    chunks: Iterable[Chunk],
) -> bytes:
    payload: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA_V2,
        "hash_algo": "blake3",
        "filename": filename,
        "size_bytes": int(size_bytes),
        "whole_file_blake3": whole_file_blake3,
        "chunking": {
            "algo": chunking.algo,
            "avg": int(chunking.avg),
            "min": int(chunking.min),
            "max": int(chunking.max),
        },
        "chunks": [{"hash": c.content_hash, "size": int(c.size)} for c in chunks],
    }
    s = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return s.encode("utf-8")


def build_manifest_v3(
    *,
    filename: str,
    size_bytes: int,
    whole_file_blake3: str,
    chunking: ChunkingConfig,
    segments: Iterable[dict[str, Any]],
    omni: dict[str, Any] | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA_V3,
        "hash_algo": "blake3",
        "filename": filename,
        "size_bytes": int(size_bytes),
        "whole_file_blake3": whole_file_blake3,
        "chunking": {
            "algo": chunking.algo,
            "avg": int(chunking.avg),
            "min": int(chunking.min),
            "max": int(chunking.max),
        },
        "segments": list(segments),
    }
    if omni is not None:
        payload["omni"] = omni
    s = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return s.encode("utf-8")


def manifest_blake3(manifest_bytes: bytes) -> str:
    # Hard-cutover: content IDs are BLAKE3 digests.
    return blake3(manifest_bytes).hexdigest()


def manifest_sha256(manifest_bytes: bytes) -> str:
    """Backward-compatible alias: returns BLAKE3 manifest content ID."""
    return manifest_blake3(manifest_bytes)


def parse_manifest(manifest_bytes: bytes) -> ManifestV1:
    data = json.loads(manifest_bytes.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Manifest must be an object")
    schema = data.get("schema")
    if schema not in (MANIFEST_SCHEMA_V1, MANIFEST_SCHEMA_V2, MANIFEST_SCHEMA_V3):
        raise ValueError("Unsupported manifest schema")

    chunking_raw = data.get("chunking") or {}
    cfg = ChunkingConfig(
        algo=str(chunking_raw.get("algo") or "fastcdc"),
        avg=int(chunking_raw.get("avg") or 1024 * 1024),
        min=int(chunking_raw.get("min") or 256 * 1024),
        max=int(chunking_raw.get("max") or 4 * 1024 * 1024),
    )
    chunks: list[ManifestChunk] = []
    segments: list[ManifestSegment] | None = None

    if schema in (MANIFEST_SCHEMA_V1, MANIFEST_SCHEMA_V2):
        chunks_raw = data.get("chunks") or []
        if not isinstance(chunks_raw, list):
            raise ValueError("manifest chunks must be a list")
        for it in chunks_raw:
            if not isinstance(it, dict):
                raise ValueError("manifest chunk must be an object")
            h = str(it.get("hash") or "").strip().lower()
            sz = int(it.get("size") or 0)
            if not h or sz < 0:
                raise ValueError("manifest chunk missing hash/size")
            chunks.append(ManifestChunk(hash=h, size=sz))
    else:
        seg_raw = data.get("segments") or []
        if not isinstance(seg_raw, list):
            raise ValueError("manifest segments must be a list")
        segments = []
        for it in seg_raw:
            if not isinstance(it, dict):
                raise ValueError("manifest segment must be an object")
            kind = str(it.get("kind") or "").strip().lower()
            if kind == "raw_chunk":
                h = str(it.get("hash") or "").strip().lower()
                sz = int(it.get("size") or 0)
                if not h or sz < 0:
                    raise ValueError("raw_chunk segment missing hash/size")
                chunks.append(ManifestChunk(hash=h, size=sz))
                segments.append(ManifestSegmentRawChunk(kind="raw_chunk", hash=h, size=sz))
            elif kind == "zstd_dict_patch":
                parent_content_id = str(it.get("parent_content_id") or "").strip().lower()
                dict_hash = str(it.get("dict_hash") or "").strip().lower()
                patch_hash = str(it.get("patch_hash") or "").strip().lower()
                range_start = int(it.get("range_start") or 0)
                range_end = int(it.get("range_end") or 0)
                patch_size = int(it.get("patch_size") or 0)
                uncompressed_size = int(it.get("uncompressed_size") or 0)
                if (
                    not parent_content_id
                    or not dict_hash
                    or not patch_hash
                    or range_start < 0
                    or range_end < range_start
                    or patch_size < 0
                    or uncompressed_size < 0
                ):
                    raise ValueError("zstd_dict_patch segment is invalid")
                segments.append(
                    ManifestSegmentZstdDictPatch(
                        kind="zstd_dict_patch",
                        parent_content_id=parent_content_id,
                        range_start=range_start,
                        range_end=range_end,
                        dict_hash=dict_hash,
                        patch_hash=patch_hash,
                        patch_size=patch_size,
                        uncompressed_size=uncompressed_size,
                    )
                )
            else:
                raise ValueError(f"Unsupported segment kind: {kind}")

    return ManifestV1(
        schema=str(schema),
        filename=str(data.get("filename") or ""),
        size_bytes=int(data.get("size_bytes") or 0),
        whole_file_blake3=str(
            data.get("whole_file_blake3")
            or data.get("whole_file_sha256")
            or ""
        ).strip().lower(),
        chunking=cfg,
        chunks=chunks,
        segments=segments,
        hash_algo=str(data.get("hash_algo") or "blake3").strip().lower(),
    )


def parse_manifest_v1(manifest_bytes: bytes) -> ManifestV1:
    return parse_manifest(manifest_bytes)


def try_parse_manifest(manifest_bytes: bytes) -> Optional[ManifestV1]:
    try:
        return parse_manifest(manifest_bytes)
    except Exception:
        return None

