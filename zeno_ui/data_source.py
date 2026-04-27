"""Cached, cancellable data source for palette / navigator dialogs.

Goals:
- Never block the UI thread on HTTP.
- Deduplicate identical requests (one keystroke == one fetch if text unchanged).
- Cache project lists indefinitely per process (refresh button invalidates).
- Cache asset lists per project id.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from zeno_client.palette_catalog import filter_assets

from zeno_ui.workflows import (
    PaletteRefreshResult,
    list_assets_for_project,
    list_projects_for_navigator,
)


@dataclass
class _CacheBucket:
    projects: list[dict[str, Any]] | None = None
    assets_by_pid: dict[str, list[dict[str, Any]]] | None = None
    version_groups_by_asset: dict[str, list[int]] | None = None


class ZenoDataCache:
    """Thread-safe, per-process cache; invalidate via ``clear()``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bucket = _CacheBucket(
            projects=None,
            assets_by_pid={},
            version_groups_by_asset={},
        )

    def clear(self) -> None:
        with self._lock:
            self._bucket = _CacheBucket(
                projects=None,
                assets_by_pid={},
                version_groups_by_asset={},
            )

    def get_projects(self, client: Any) -> list[dict[str, Any]]:
        with self._lock:
            cached = self._bucket.projects
        if cached is not None:
            return cached
        fetched = list_projects_for_navigator(client)
        with self._lock:
            self._bucket.projects = fetched
        return fetched

    def get_assets(self, client: Any, project_id: str) -> list[dict[str, Any]]:
        if not project_id:
            return []
        with self._lock:
            cached = (self._bucket.assets_by_pid or {}).get(project_id)
        if cached is not None:
            return cached
        fetched = list_assets_for_project(client, project_id)
        with self._lock:
            if self._bucket.assets_by_pid is None:
                self._bucket.assets_by_pid = {}
            self._bucket.assets_by_pid[project_id] = fetched
        return fetched

    def get_version_groups(self, client: Any, asset_id: str) -> list[int]:
        if not asset_id:
            return []
        with self._lock:
            cached = (self._bucket.version_groups_by_asset or {}).get(asset_id)
        if cached is not None:
            return cached
        try:
            groups = client.list_asset_version_groups(asset_id)
        except Exception:
            groups = []
        nums: list[int] = []
        for g in groups:
            try:
                nums.append(int(g.get("version_number")))
            except Exception:
                pass
        nums.sort(reverse=True)
        with self._lock:
            if self._bucket.version_groups_by_asset is None:
                self._bucket.version_groups_by_asset = {}
            self._bucket.version_groups_by_asset[asset_id] = nums
        return nums


_GLOBAL_CACHE = ZenoDataCache()


def global_cache() -> ZenoDataCache:
    return _GLOBAL_CACHE


def refresh_palette_state_cached(
    cache: ZenoDataCache,
    client: Any,
    *,
    project_code: str,
    query: str,
    asset_code: str,
    version: str,
) -> PaletteRefreshResult:
    """Cache-aware mirror of ``workflows.refresh_palette_state``.

    Uses cached projects/assets instead of re-fetching on every keystroke.
    Only the version-group lookup hits the network when the asset changes.
    """
    project_code = (project_code or "").strip()
    projects = cache.get_projects(client)
    project = None
    if project_code:
        for p in projects:
            if p["code"] == project_code:
                project = p
                break
    if project is None:
        return PaletteRefreshResult([], [], asset_code, (version or "latest").strip() or "latest")

    assets = cache.get_assets(client, project["id"])
    filtered = filter_assets(assets, query.strip())

    valid_codes = [str(a.get("code") or "").strip() for a in filtered if str(a.get("code") or "").strip()]
    ac = asset_code.strip()
    if valid_codes and ac not in valid_codes:
        ac = valid_codes[0]
    ver = (version or "latest").strip() or "latest"

    if not filtered:
        return PaletteRefreshResult([], [], ac, ver)

    asset: dict[str, Any] | None = None
    for a in filtered:
        if str(a.get("code") or "").strip() == ac:
            asset = a
            break
    if not asset:
        return PaletteRefreshResult(filtered, [], ac, ver)

    # ``list_assets_for_project`` wraps the raw API row under "raw"; asset id lives there.
    raw = asset.get("raw") if isinstance(asset.get("raw"), dict) else asset
    asset_id = str(raw.get("id") or "")
    nums = cache.get_version_groups(client, asset_id)
    return PaletteRefreshResult(filtered, nums, ac, ver)


def run_in_background(
    call: Callable[[], Any],
    on_done: Callable[[Any], None],
    on_error: Callable[[BaseException], None] | None = None,
) -> None:
    """Run ``call`` on a background thread; invoke ``on_done`` on UI thread.

    Intended for use from Qt slots: ``on_done`` should marshal back via
    ``QMetaObject.invokeMethod`` or a ``QObject`` signal. This helper only
    crosses the thread boundary at the Python level; the caller wires the
    Qt-side marshalling (see ``qt_workers.QtAsyncRunner``).
    """

    def _worker() -> None:
        try:
            result = call()
        except BaseException as exc:  # noqa: BLE001 - propagate
            if on_error is not None:
                on_error(exc)
            return
        on_done(result)

    t = threading.Thread(target=_worker, name="zeno-ui-bg", daemon=True)
    t.start()


__all__ = [
    "ZenoDataCache",
    "global_cache",
    "refresh_palette_state_cached",
    "run_in_background",
]
