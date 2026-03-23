from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

from .chunking import Chunk, ChunkingConfig


MANIFEST_SCHEMA_V1 = "chimera.manifest.v1"


@dataclass(frozen=True)
class ManifestChunk:
    hash: str
    size: int


@dataclass(frozen=True)
class ManifestV1:
    schema: str
    filename: str
    size_bytes: int
    whole_file_sha256: str
    chunking: ChunkingConfig
    chunks: List[ManifestChunk]


def build_manifest_v1(
    *,
    filename: str,
    size_bytes: int,
    whole_file_sha256: str,
    chunking: ChunkingConfig,
    chunks: Iterable[Chunk],
) -> bytes:
    payload: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA_V1,
        "filename": filename,
        "size_bytes": int(size_bytes),
        "whole_file_sha256": whole_file_sha256,
        "chunking": {
            "algo": chunking.algo,
            "avg": int(chunking.avg),
            "min": int(chunking.min),
            "max": int(chunking.max),
        },
        "chunks": [{"hash": c.sha256, "size": int(c.size)} for c in chunks],
    }
    # Deterministic encoding so sha256(manifest bytes) is stable
    s = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return s.encode("utf-8")


def manifest_sha256(manifest_bytes: bytes) -> str:
    return hashlib.sha256(manifest_bytes).hexdigest()


def parse_manifest_v1(manifest_bytes: bytes) -> ManifestV1:
    data = json.loads(manifest_bytes.decode("utf-8"))
    if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA_V1:
        raise ValueError("Not a chimera.manifest.v1 manifest")

    chunking_raw = data.get("chunking") or {}
    cfg = ChunkingConfig(
        algo=str(chunking_raw.get("algo") or "fastcdc"),
        avg=int(chunking_raw.get("avg") or 1024 * 1024),
        min=int(chunking_raw.get("min") or 256 * 1024),
        max=int(chunking_raw.get("max") or 4 * 1024 * 1024),
    )
    chunks_raw = data.get("chunks") or []
    if not isinstance(chunks_raw, list):
        raise ValueError("manifest chunks must be a list")
    chunks: list[ManifestChunk] = []
    for it in chunks_raw:
        if not isinstance(it, dict):
            raise ValueError("manifest chunk must be an object")
        h = str(it.get("hash") or "").strip().lower()
        sz = int(it.get("size") or 0)
        if not h or sz < 0:
            raise ValueError("manifest chunk missing hash/size")
        chunks.append(ManifestChunk(hash=h, size=sz))

    return ManifestV1(
        schema=MANIFEST_SCHEMA_V1,
        filename=str(data.get("filename") or ""),
        size_bytes=int(data.get("size_bytes") or 0),
        whole_file_sha256=str(data.get("whole_file_sha256") or "").strip().lower(),
        chunking=cfg,
        chunks=chunks,
    )


def try_parse_manifest(manifest_bytes: bytes) -> Optional[ManifestV1]:
    try:
        return parse_manifest_v1(manifest_bytes)
    except Exception:
        return None

