from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import httpx

from ._http import _join, parse_json, raise_for_status


DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _default_base_url() -> str:
    return (
        os.environ.get("ZENO_API_BASE_URL")
        or os.environ.get("CHIMERA_API_BASE_URL")
        or DEFAULT_BASE_URL
    ).strip()


class ZenoClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: httpx.Timeout | None = None,
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = (base_url or _default_base_url()).rstrip("/")
        self._timeout = timeout or httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)
        self._headers = headers or {}
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout, headers=self._headers, transport=self._transport)

    # --- Resolver ---
    def resolve(self, uri: str) -> dict[str, Any]:
        with self._client() as c:
            resp = c.get(_join(self.base_url, "/api/v1/resolve"), params={"uri": uri})
            data = parse_json(resp, operation="resolve")
            assert isinstance(data, dict)
            return data

    def latest_content_id(
        self,
        *,
        project: str,
        asset: str,
        representation: str,
        artifact: str = "delivery",
    ) -> str | None:
        """
        Resolve latest CAS content id for a representation.

        artifact:
          - delivery (default): primary blob for resolver/DCC load (raw file for dual-artifact blend).
          - dedup: canonical manifest id from versions.metadata.dedup_artifact when present (Omni parent).
        """
        with self._client() as c:
            resp = c.get(
                _join(self.base_url, "/api/v1/versions/latest-content"),
                params={
                    "project": project,
                    "asset": asset,
                    "representation": representation,
                    "artifact": artifact,
                },
            )
            if resp.status_code == 404:
                return None
            data = parse_json(resp, operation="latest_content_id")
            if not isinstance(data, dict):
                return None
            cid = str(data.get("content_id") or "").strip().lower()
            return cid or None

    # --- CAS blobs ---
    def blob_exists(self, content_hash: str) -> bool:
        h = content_hash.strip().lower()
        with self._client() as c:
            resp = c.get(_join(self.base_url, f"/api/v1/cas/blobs/{h}/exists"))
            if resp.status_code == 404:
                return False
            data = parse_json(resp, operation="blob_exists")
            return bool(isinstance(data, dict) and data.get("exists") is True)

    def upload_blob(self, path: str | Path, content_hash: str) -> bool:
        p = Path(path)
        h = content_hash.strip().lower()
        headers = {"X-Content-Hash": h}

        def file_iter():
            with p.open("rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk

        with self._client() as c:
            resp = c.post(_join(self.base_url, "/api/v1/cas/blobs"), headers=headers, content=file_iter())
            if resp.status_code in (200, 201):
                return resp.status_code == 201
            raise_for_status(resp, operation="upload_blob")
            return False  # unreachable

    def upload_blob_bytes(self, body: bytes, content_hash: str) -> bool:
        h = content_hash.strip().lower()
        headers = {"X-Content-Hash": h}
        with self._client() as c:
            resp = c.post(_join(self.base_url, "/api/v1/cas/blobs"), headers=headers, content=body)
            if resp.status_code in (200, 201):
                return resp.status_code == 201
            raise_for_status(resp, operation="upload_blob")
            return False  # unreachable

    def get_blob(self, content_hash: str, dest_path: str | Path) -> None:
        h = content_hash.strip().lower()
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._client() as c:
            with c.stream("GET", _join(self.base_url, f"/api/v1/cas/blobs/{h}")) as resp:
                if resp.status_code != 200:
                    raise_for_status(resp, operation="get_blob")
                tmp = dest.with_suffix(dest.suffix + ".tmp")
                with tmp.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
                tmp.replace(dest)

    def head_blob(self, content_hash: str) -> int | None:
        """Return blob size via HEAD, or None if unknown."""
        h = content_hash.strip().lower()
        with self._client() as c:
            resp = c.head(_join(self.base_url, f"/api/v1/cas/blobs/{h}"))
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                raise_for_status(resp, operation="head_blob")
            cl = resp.headers.get("Content-Length")
            try:
                return int(cl) if cl is not None else None
            except Exception:
                return None

    def get_blob_bytes(self, content_hash: str, *, max_bytes: int | None = None) -> bytes:
        """
        Download blob into memory. If max_bytes is set, reads at most that many bytes.
        Useful for small manifest blobs or header sniffing.
        """
        h = content_hash.strip().lower()
        out = bytearray()
        with self._client() as c:
            with c.stream("GET", _join(self.base_url, f"/api/v1/cas/blobs/{h}")) as resp:
                if resp.status_code != 200:
                    raise_for_status(resp, operation="get_blob")
                for chunk in resp.iter_bytes():
                    out.extend(chunk)
                    if max_bytes is not None and len(out) >= max_bytes:
                        return bytes(out[:max_bytes])
        return bytes(out)

    # --- Register version ---
    def register_version(
        self,
        *,
        project: str,
        asset: str,
        representation: str,
        version: str,
        content_id: str,
        filename: str | None = None,
        size: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "project": project,
            "asset": asset,
            "representation": representation,
            "version": version,
            "content_id": content_id.strip().lower(),
        }
        if filename is not None:
            body["filename"] = filename
        if size is not None:
            body["size"] = size
        if metadata is not None:
            body["metadata"] = metadata
        with self._client() as c:
            resp = c.post(_join(self.base_url, "/api/v1/versions"), json=body)
            data = parse_json(resp, operation="register_version")
            assert isinstance(data, dict)
            return data

    # --- Presence ---
    def heartbeat(
        self,
        *,
        user_id: str,
        session_id: str,
        project: str | None = None,
        asset: str | None = None,
        representation: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        body: dict[str, Any] = {"user_id": user_id, "session_id": session_id}
        if project is not None:
            body["project"] = project
        if asset is not None:
            body["asset"] = asset
        if representation is not None:
            body["representation"] = representation
        if metadata is not None:
            body["metadata"] = metadata
        with self._client() as c:
            resp = c.post(_join(self.base_url, "/api/v1/presence/heartbeat"), json=body)
            parse_json(resp, operation="presence_heartbeat")

    def list_sessions(self, *, user_id: str) -> list[dict[str, Any]]:
        with self._client() as c:
            resp = c.get(_join(self.base_url, "/api/v1/presence/sessions"), params={"user_id": user_id})
            data = parse_json(resp, operation="presence_sessions")
            assert isinstance(data, list)
            return data

    # --- Locks ---
    def acquire_lock(
        self,
        *,
        user_id: str,
        session_id: str,
        project: str,
        asset: str,
        representation: str,
    ) -> dict[str, Any]:
        body = {
            "user_id": user_id,
            "session_id": session_id,
            "project": project,
            "asset": asset,
            "representation": representation,
        }
        with self._client() as c:
            resp = c.post(_join(self.base_url, "/api/v1/locks/acquire"), json=body)
            data = parse_json(resp, operation="lock_acquire")
            assert isinstance(data, dict)
            return data

    def release_lock(
        self,
        *,
        user_id: str,
        session_id: str,
        project: str,
        asset: str,
        representation: str,
    ) -> None:
        body = {
            "user_id": user_id,
            "session_id": session_id,
            "project": project,
            "asset": asset,
            "representation": representation,
        }
        with self._client() as c:
            resp = c.post(_join(self.base_url, "/api/v1/locks/release"), json=body)
            parse_json(resp, operation="lock_release")

    def lock_status(self, *, project: str, asset: str, representation: str) -> Optional[dict[str, Any]]:
        with self._client() as c:
            resp = c.get(
                _join(self.base_url, "/api/v1/locks/status"),
                params={"project": project, "asset": asset, "representation": representation},
            )
            if resp.status_code == 404:
                return None
            data = parse_json(resp, operation="lock_status")
            assert isinstance(data, dict)
            return data


_default_client: ZenoClient | None = None


def default_client() -> ZenoClient:
    global _default_client
    if _default_client is None:
        _default_client = ZenoClient()
    return _default_client

