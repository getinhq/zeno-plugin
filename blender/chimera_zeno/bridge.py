from __future__ import annotations

import uuid
from typing import Any

import bpy

def addon_prefs() -> Any:
    addon = bpy.context.preferences.addons.get(__package__.split(".")[0])
    return addon.preferences if addon else None


def make_client():
    from zeno_client import ZenoClient

    prefs = addon_prefs()
    base_url = getattr(prefs, "api_base_url", "").strip() if prefs else ""
    return ZenoClient(base_url=base_url or None)


def make_cache():
    from zeno_client import CacheConfig, LocalCache

    prefs = addon_prefs()
    max_gb = int(getattr(prefs, "cache_max_gb", 50) or 50) if prefs else 50
    return LocalCache(CacheConfig(max_bytes=max_gb * 1024 * 1024 * 1024))


def build_asset_uri(project: str, asset: str, version: str, representation: str) -> str:
    from zeno_client.palette_catalog import build_asset_uri as _build_asset_uri

    return _build_asset_uri(project, asset, version, representation)


def filter_assets(assets, query: str):
    from zeno_client.palette_catalog import filter_assets as _filter_assets

    return _filter_assets(assets, query)


def publish_chunked_file(*args, **kwargs):
    from zeno_client.publisher import publish_chunked_file as _publish_chunked_file

    return _publish_chunked_file(*args, **kwargs)


def heartbeat(client, *, project: str | None = None, asset: str | None = None) -> None:
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

