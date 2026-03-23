from __future__ import annotations

from typing import Any

import httpx

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


def _join(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    p = path if path.startswith("/") else f"/{path}"
    return f"{base}{p}"


def _detail_from_response(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("detail"), str):
            return data["detail"]
    except Exception:
        pass
    return resp.text or resp.reason_phrase


def raise_for_status(resp: httpx.Response, *, operation: str) -> None:
    if 200 <= resp.status_code < 300:
        return

    detail = _detail_from_response(resp)
    msg = f"{operation} failed ({resp.status_code}): {detail}"

    if resp.status_code == 503:
        raise ServiceUnavailable(msg, status_code=resp.status_code, detail=detail)

    if resp.status_code == 400:
        # Special cases
        dlow = (detail or "").lower()
        if "hash" in dlow and "invalid" in dlow:
            raise InvalidHash(msg, status_code=resp.status_code, detail=detail)
        if "mismatch" in dlow:
            raise ContentHashMismatch(msg, status_code=resp.status_code, detail=detail)
        if operation.startswith("resolve"):
            raise ResolveBadRequest(msg, status_code=resp.status_code, detail=detail)
        raise BadRequest(msg, status_code=resp.status_code, detail=detail)

    if resp.status_code == 404:
        if operation.startswith("resolve"):
            raise ResolveNotFound(msg, status_code=resp.status_code, detail=detail)
        if operation.startswith("get_blob") or operation.startswith("blob_exists") or operation.startswith("head_blob"):
            raise BlobNotFound(msg, status_code=resp.status_code, detail=detail)
        if operation.startswith("lock"):
            raise LockNotFound(msg, status_code=resp.status_code, detail=detail)
        raise NotFound(msg, status_code=resp.status_code, detail=detail)

    if resp.status_code == 403:
        if operation.startswith("lock"):
            raise LockNotOwned(msg, status_code=resp.status_code, detail=detail)
        raise Forbidden(msg, status_code=resp.status_code, detail=detail)

    if resp.status_code == 409:
        if operation.startswith("lock"):
            raise LockHeldByOther(msg, status_code=resp.status_code, detail=detail)
        if operation.startswith("register_version"):
            dlow = (detail or "").lower()
            if "cas" in dlow or "content" in dlow:
                raise RegisterContentNotFound(msg, status_code=resp.status_code, detail=detail)
            raise RegisterVersionConflict(msg, status_code=resp.status_code, detail=detail)
        raise Conflict(msg, status_code=resp.status_code, detail=detail)

    raise ZenoAPIError(msg, status_code=resp.status_code, detail=detail)


def parse_json(resp: httpx.Response, *, operation: str) -> Any:
    raise_for_status(resp, operation=operation)
    if resp.status_code == 204:
        return None
    try:
        return resp.json()
    except Exception as e:
        detail = f"Invalid JSON response: {e}"
        raise ZenoAPIError(f"{operation} failed: {detail}", status_code=resp.status_code, detail=detail) from e

