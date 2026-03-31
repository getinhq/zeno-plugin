from __future__ import annotations

import uuid
from typing import Any

import bpy

from zeno_client import CacheConfig, LocalCache, ZenoClient
from zeno_client.palette_catalog import build_asset_uri, filter_assets
from zeno_client.publisher import publish_chunked_file


def addon_prefs() -> Any:
    addon = bpy.context.preferences.addons.get(__package__.split(".")[0])
    return addon.preferences if addon else None


def make_client() -> ZenoClient:
    prefs = addon_prefs()
    base_url = getattr(prefs, "api_base_url", "").strip() if prefs else ""
    return ZenoClient(base_url=base_url or None)


def make_cache() -> LocalCache:
    prefs = addon_prefs()
    max_gb = int(getattr(prefs, "cache_max_gb", 50) or 50) if prefs else 50
    return LocalCache(CacheConfig(max_bytes=max_gb * 1024 * 1024 * 1024))


def heartbeat(client: ZenoClient, *, project: str | None = None, asset: str | None = None) -> None:
    prefs = addon_prefs()
    if not prefs:
        return
    sid = getattr(prefs, "session_id", "").strip()
    uid = getattr(prefs, "user_id", "").strip()
    if not sid:
        sid = str(uuid.uuid4())
        prefs.session_id = sid
    if not uid:
        uid = "blender_user"
    client.heartbeat(user_id=uid, session_id=sid, project=project, asset=asset, representation="blend")


__all__ = [
    "addon_prefs",
    "build_asset_uri",
    "filter_assets",
    "heartbeat",
    "make_cache",
    "make_client",
    "publish_chunked_file",
]

