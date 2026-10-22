from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ._hash import compute_content_hash
from .chunking import ChunkingConfig, iter_chunks
from .client import ZenoClient
from .dcc_registry import canonicalize_file
from .entropy_segment import EntropyConfig
from .manifest import build_manifest_v2, manifest_blake3
from .omni_ingest import ingest_omni_file


@dataclass(frozen=True)
class PublishChunkedResult:
    manifest_id: str
    whole_file_blake3: str
    chunk_count: int
    uploaded_chunks: int
    registered_version: dict[str, Any]
    # Dual-artifact (Omni + DCC canonical): delivery = raw file hash; dedup = manifest_id
    delivery_content_id: Optional[str] = None
    dedup_manifest_id: Optional[str] = None
    # Storage workflow used for this publish ("chunked" or "whole") and why.
    strategy: str = "chunked"
    strategy_reason: str = ""

    @property
    def whole_file_sha256(self) -> str:
        """Deprecated alias: returns BLAKE3 whole-file hash."""
        return self.whole_file_blake3


def resolve_strategy(client: ZenoClient, filename: str) -> tuple[str, str, str]:
    """(strategy, reason, algorithm_version) from the studio's storage workflow.

    Falls back to chunked (the historical behaviour) when the server has no policy endpoint
    or cannot be asked, so older servers keep working.
    """
    try:
        decision = client.resolve_storage(filename)
    except Exception as exc:  # noqa: BLE001
        return "chunked", f"storage policy unavailable ({type(exc).__name__}); using chunked", ""
    if isinstance(decision, dict) and decision.get("strategy") in ("chunked", "whole"):
        return decision["strategy"], str(decision.get("reason") or ""), str(decision.get("algorithm_version") or "")
    return "chunked", "no usable storage policy; using chunked", ""


def _publish_whole_file(
    *, client: ZenoClient, p: Path, whole: str, size_bytes: int, fname: str, rep: str, project: str,
    asset: str, version: str, pipeline_stage: str, task_id: str | None, storage_meta: dict,
) -> PublishChunkedResult:
    """One blob for the whole file (identical files are still stored once)."""
    uploaded = 0
    if not client.blob_exists(whole):
        client.upload_blob(p, whole)
        uploaded = 1
    reg = client.register_version(
        project=project,
        asset=asset,
        representation=rep,
        version=version,
        content_id=whole,
        filename=fname,
        size=size_bytes,
        metadata={"storage": storage_meta},
        pipeline_stage=pipeline_stage,
        task_id=task_id,
    )
    return PublishChunkedResult(
        manifest_id=whole,
        whole_file_blake3=whole,
        chunk_count=1,
        uploaded_chunks=uploaded,
        registered_version=reg,
        strategy="whole",
        strategy_reason=storage_meta.get("reason", ""),
    )


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
    use_omni: bool | None = None,
    parent_content_id: str | None = None,
    parent_local_path: str | Path | None = None,
    entropy: EntropyConfig | None = None,
    dcc: str | None = None,
    pipeline_stage: str = "",
    task_id: str | None = None,
    strategy: str | None = None,
) -> PublishChunkedResult:
    """
    Publish a file following the studio's storage workflow (chunked or whole file).

    ``strategy`` forces "chunked" or "whole"; by default it is asked from the server
    (Settings → Storage workflow), per file extension.

    Note:
        representation is normalized to the file extension (for example,
        ".blend" -> "blend") to keep registry keys consistent.

    Args:
        dcc: Optional DCC name (e.g. "blender"). When set, the file is
             pre-canonicalized (DCC-specific volatility stripped) before
             chunking, producing significantly higher deduplication rates.
             Supported: "blender", "maya" (Maya ASCII ``.ma`` only). Coming soon: more DCCs.
    """
    p = Path(path)
    chunking = chunking or ChunkingConfig()
    fname = filename or p.name
    # Canonical representation key is always file extension.
    rep = p.suffix.lstrip(".").lower() or (representation or "").strip().lower().lstrip(".")

    whole = compute_content_hash(p)
    size_bytes = int(p.stat().st_size)

    if strategy in ("chunked", "whole"):
        reason, algo = "requested by caller", ""
    else:
        strategy, reason, algo = resolve_strategy(client, fname)
    storage_meta = {"strategy": strategy, "reason": reason, "algorithm_version": algo}
    if strategy == "whole":
        return _publish_whole_file(
            client=client, p=p, whole=whole, size_bytes=size_bytes, fname=fname, rep=rep, project=project,
            asset=asset, version=version, pipeline_stage=pipeline_stage, task_id=task_id, storage_meta=storage_meta,
        )

    if use_omni is None:
        use_omni = str(os.environ.get("OMNI_CHUNKER", "0")).strip().lower() in ("1", "true", "yes", "on")

    # ── DCC Canonicalization ────────────────────────────────────────────────
    # Detect DCC from extension if not explicitly given, then canonicalize.
    # Returns None for unsupported types → raw bytes used (zero behaviour change).
    resolved_dcc = dcc or ""
    canonical_bytes: bytes | None = canonicalize_file(p, dcc_hint=dcc)
    if canonical_bytes is not None and not resolved_dcc:
        resolved_dcc = p.suffix.lstrip(".")
    # ────────────────────────────────────────────────────────────────────────

    if use_omni:
        if parent_content_id is None:
            parent_content_id = client.latest_content_id(
                project=project, asset=asset, representation=rep, artifact="dedup"
            )
        omni = ingest_omni_file(
            client=client,
            path=p,
            filename=fname,
            chunking=chunking,
            entropy_cfg=entropy,
            parent_content_id=parent_content_id,
            parent_local_path=parent_local_path,
            canonical_bytes=canonical_bytes,
            dcc=resolved_dcc or None,
        )
        # Dual-artifact: raw delivery blob + canonical dedup manifest (DCC canonical path only)
        if canonical_bytes is not None:
            delivery_hash = whole
            if not client.blob_exists(delivery_hash):
                client.upload_blob(p, delivery_hash)
            meta = {
                "storage": storage_meta,
                "dedup_artifact": {
                    "content_id": omni.manifest_id,
                    "schema": "chimera.manifest.v3",
                    "dcc_canonical": omni.dcc_canonical,
                    "dcc": (resolved_dcc or ""),
                },
            }
            reg = client.register_version(
                project=project,
                asset=asset,
                representation=rep,
                version=version,
                content_id=delivery_hash,
                filename=fname,
                size=size_bytes,
                metadata=meta,
                pipeline_stage=pipeline_stage,
                task_id=task_id,
            )
            return PublishChunkedResult(
                manifest_id=omni.manifest_id,
                whole_file_blake3=omni.whole_file_blake3,
                chunk_count=len(omni.segments),
                uploaded_chunks=omni.uploaded_chunks + omni.uploaded_aux_blobs,
                registered_version=reg,
                delivery_content_id=delivery_hash,
                dedup_manifest_id=omni.manifest_id,
                strategy="chunked",
                strategy_reason=reason,
            )
        reg = client.register_version(
            project=project,
            asset=asset,
            representation=rep,
            version=version,
            content_id=omni.manifest_id,
            filename=fname,
            size=size_bytes,
            metadata={"storage": storage_meta},
            pipeline_stage=pipeline_stage,
            task_id=task_id,
        )
        return PublishChunkedResult(
            manifest_id=omni.manifest_id,
            whole_file_blake3=omni.whole_file_blake3,
            chunk_count=len(omni.segments),
            uploaded_chunks=omni.uploaded_chunks + omni.uploaded_aux_blobs,
            registered_version=reg,
            strategy="chunked",
            strategy_reason=reason,
        )

    # Upload missing chunks
    uploaded_chunks = 0
    chunks = []

    with p.open("rb") as f:
        # We iterate chunks and re-read bytes for each chunk from the file (bounded by max chunk size).
        # iter_chunks already reads, but doesn't return bytes; we re-read via offsets to keep memory bounded.
        for ch in iter_chunks(p, cfg=chunking):
            chunks.append(ch)
            if client.blob_exists(ch.content_hash):
                continue
            f.seek(ch.offset)
            body = f.read(ch.size)
            client.upload_blob_bytes(body, ch.content_hash)
            uploaded_chunks += 1

    manifest_bytes = build_manifest_v2(
        filename=fname,
        size_bytes=size_bytes,
        whole_file_blake3=whole,
        chunking=chunking,
        chunks=chunks,
    )
    mid = manifest_blake3(manifest_bytes)

    # Upload manifest (idempotent)
    client.upload_blob_bytes(manifest_bytes, mid)

    # Register version points to manifest
    reg = client.register_version(
        project=project,
        asset=asset,
        representation=rep,
        version=version,
        content_id=mid,
        filename=fname,
        size=size_bytes,
        metadata={"storage": storage_meta},
        pipeline_stage=pipeline_stage,
        task_id=task_id,
    )

    return PublishChunkedResult(
        manifest_id=mid,
        whole_file_blake3=whole,
        chunk_count=len(chunks),
        uploaded_chunks=uploaded_chunks,
        registered_version=reg,
        strategy="chunked",
        strategy_reason=reason,
    )


def get_restore_path(
    work_dir: str | Path,
    asset: str,
    version: str | int,
    filename: str,
) -> Path:
    """
    Returns the canonical restore destination path for a published asset version.

    Convention: {work_dir}/{asset}/v{version:04d}/{filename}

    Example:
        get_restore_path("/jobs/proj/assets", "nono_dildo", 2, "nono_dildo_v002.blend")
        → Path("/jobs/proj/assets/nono_dildo/v0002/nono_dildo_v002.blend")
    """
    ver_str = f"v{int(version):04d}" if str(version).isdigit() else str(version)
    out = Path(work_dir) / asset / ver_str / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    return out

