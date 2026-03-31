from __future__ import annotations

"""Zeno API client — shared Python library for DCC plugins (resolve, upload, register, session)."""

from ._hash import compute_blake3, compute_content_hash, compute_sha256
from .cache import CacheConfig, LocalCache
from .cache_exceptions import CacheCorruptError, CacheError, CacheLockTimeoutError
from .client import ZenoClient, default_client
from .entropy_segment import EntropyConfig
from .omni_ingest import OmniIngestResult, ingest_omni_file, materialize_from_manifest_v3
from .palette_catalog import build_asset_uri, filter_assets
from .publisher import PublishChunkedResult, publish_chunked_file
from .launch_context import LaunchContextV1, read_launch_context_from_environ, apply_api_base_url_to_environ
from .exceptions import (
    BadRequest,
    BlobNotFound,
    Conflict,
    ContentHashMismatch,
    Forbidden,
    InvalidHash,
    LockHeldByOther,
    LockNotFound,
    LockNotOwned,
    NotFound,
    RegisterContentNotFound,
    RegisterVersionConflict,
    ResolveBadRequest,
    ResolveNotFound,
    ServiceUnavailable,
    ZenoAPIError,
)

__version__ = "0.2.0"


def resolve(uri: str):
    return default_client().resolve(uri)


def list_projects(*, status: str | None = None, code: str | None = None):
    return default_client().list_projects(status=status, code=code)


def list_assets(project_id: str, *, code: str | None = None, type: str | None = None):
    return default_client().list_assets(project_id, code=code, type=type)


def list_asset_version_groups(asset_id: str):
    return default_client().list_asset_version_groups(asset_id)


def blob_exists(content_hash: str) -> bool:
    return default_client().blob_exists(content_hash)


def upload_blob(path, content_hash: str) -> bool:
    return default_client().upload_blob(path, content_hash)


def get_blob(content_hash: str, dest_path) -> None:
    return default_client().get_blob(content_hash, dest_path)


def register_version(
    *,
    project: str,
    asset: str,
    representation: str,
    version: str,
    content_id: str,
    filename: str | None = None,
    size: int | None = None,
):
    return default_client().register_version(
        project=project,
        asset=asset,
        representation=representation,
        version=version,
        content_id=content_id,
        filename=filename,
        size=size,
    )


def heartbeat(
    *,
    user_id: str,
    session_id: str,
    project: str | None = None,
    asset: str | None = None,
    representation: str | None = None,
    metadata=None,
) -> None:
    return default_client().heartbeat(
        user_id=user_id,
        session_id=session_id,
        project=project,
        asset=asset,
        representation=representation,
        metadata=metadata,
    )


def session_register(**kwargs) -> None:
    return heartbeat(**kwargs)


def list_sessions(*, user_id: str):
    return default_client().list_sessions(user_id=user_id)


def acquire_lock(*, user_id: str, session_id: str, project: str, asset: str, representation: str):
    return default_client().acquire_lock(
        user_id=user_id,
        session_id=session_id,
        project=project,
        asset=asset,
        representation=representation,
    )


def release_lock(*, user_id: str, session_id: str, project: str, asset: str, representation: str) -> None:
    return default_client().release_lock(
        user_id=user_id,
        session_id=session_id,
        project=project,
        asset=asset,
        representation=representation,
    )


def lock_status(*, project: str, asset: str, representation: str):
    return default_client().lock_status(project=project, asset=asset, representation=representation)


__all__ = [
    "ZenoClient",
    "default_client",
    "build_asset_uri",
    "filter_assets",
    "list_projects",
    "list_assets",
    "list_asset_version_groups",
    "compute_blake3",
    "compute_content_hash",
    "compute_sha256",
    "CacheConfig",
    "LocalCache",
    "publish_chunked_file",
    "PublishChunkedResult",
    "EntropyConfig",
    "OmniIngestResult",
    "ingest_omni_file",
    "materialize_from_manifest_v3",
    "CacheError",
    "CacheLockTimeoutError",
    "CacheCorruptError",
    "ZenoAPIError",
    "ServiceUnavailable",
    "BadRequest",
    "ResolveBadRequest",
    "InvalidHash",
    "ContentHashMismatch",
    "Forbidden",
    "NotFound",
    "ResolveNotFound",
    "BlobNotFound",
    "Conflict",
    "LockHeldByOther",
    "LockNotFound",
    "LockNotOwned",
    "LaunchContextV1",
    "read_launch_context_from_environ",
    "apply_api_base_url_to_environ",
    "RegisterContentNotFound",
    "RegisterVersionConflict",
]
