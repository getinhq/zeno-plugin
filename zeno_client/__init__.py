from __future__ import annotations

"""Zeno API client — shared Python library for DCC plugins (resolve, upload, register, session)."""

from ._hash import compute_sha256
from .cache import CacheConfig, LocalCache
from .cache_exceptions import CacheCorruptError, CacheError, CacheLockTimeoutError
from .client import ZenoClient, default_client
from .publisher import PublishChunkedResult, publish_chunked_file
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
    "compute_sha256",
    "CacheConfig",
    "LocalCache",
    "publish_chunked_file",
    "PublishChunkedResult",
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
    "RegisterContentNotFound",
    "RegisterVersionConflict",
]
