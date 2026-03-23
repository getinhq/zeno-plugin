from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ._hash import compute_sha256
from .chunking import ChunkingConfig, iter_chunks
from .client import ZenoClient
from .manifest import build_manifest_v1, manifest_sha256


@dataclass(frozen=True)
class PublishChunkedResult:
    manifest_id: str
    whole_file_sha256: str
    chunk_count: int
    uploaded_chunks: int
    registered_version: dict[str, Any]


def publish_chunked_file(
    *,
    client: ZenoClient,
    project: str,
    asset: str,
    representation: str,
    path: str | Path,
    version: str = "next",
    chunking: ChunkingConfig | None = None,
    filename: str | None = None,
) -> PublishChunkedResult:
    """
    Publish a file using client-side CDC chunking:
    - Split into chunks, upload missing chunks (dedup/resume)
    - Upload manifest blob
    - Register version with content_id = manifest_id
    """
    p = Path(path)
    chunking = chunking or ChunkingConfig()
    fname = filename or p.name

    whole = compute_sha256(p)
    size_bytes = int(p.stat().st_size)

    # Upload missing chunks
    uploaded_chunks = 0
    chunks = []

    with p.open("rb") as f:
        # We iterate chunks and re-read bytes for each chunk from the file (bounded by max chunk size).
        # iter_chunks already reads, but doesn't return bytes; we re-read via offsets to keep memory bounded.
        for ch in iter_chunks(p, cfg=chunking):
            chunks.append(ch)
            if client.blob_exists(ch.sha256):
                continue
            f.seek(ch.offset)
            body = f.read(ch.size)
            client.upload_blob_bytes(body, ch.sha256)
            uploaded_chunks += 1

    manifest_bytes = build_manifest_v1(
        filename=fname,
        size_bytes=size_bytes,
        whole_file_sha256=whole,
        chunking=chunking,
        chunks=chunks,
    )
    mid = manifest_sha256(manifest_bytes)

    # Upload manifest (idempotent)
    client.upload_blob_bytes(manifest_bytes, mid)

    # Register version points to manifest
    reg = client.register_version(
        project=project,
        asset=asset,
        representation=representation,
        version=version,
        content_id=mid,
        filename=fname,
        size=size_bytes,
    )

    return PublishChunkedResult(
        manifest_id=mid,
        whole_file_sha256=whole,
        chunk_count=len(chunks),
        uploaded_chunks=uploaded_chunks,
        registered_version=reg,
    )

