"""Zeno assets inside a shot file, and what the file reports to Zeno.

- ``chimera.link_asset``: link an asset version's collections into the open file (from the
  local cache) and add an instance of each top-level one. The library is tagged with the
  Zeno identity (``library["zeno"]``), so the file can always say which version it uses.
- ``chimera.update_linked_asset``: point a linked library at another version and reload it.
- ``collect_facts``: frame range, frame rate, camera and the linked Zeno versions, sent to
  ``PUT /shots/{id}/scene-facts`` by the Submit for Review window.
- ``relink_missing``: linked files point into the local cache of whoever linked them. When
  a file opens on another machine, Zeno libraries whose file is missing are fetched into this
  machine's cache by content id (in the background), repointed and reloaded. Runs on file
  load and from Zeno > Fix Linked Assets.
"""
from __future__ import annotations

import os
import threading
from typing import Any

import bpy
from bpy.app.handlers import persistent

from zeno_client.review import fps_string
from zeno_client.scene_facts import library_identity, missing_libraries

from .bridge import addon_prefs, build_asset_uri, make_cache, make_client

TAG = "zeno"


def _library_for(path: str):  # pragma: no cover - Blender runtime
    want = bpy.path.abspath(path)
    return next((lib for lib in bpy.data.libraries if bpy.path.abspath(lib.filepath) == want), None)


def _tag(lib) -> dict[str, Any]:  # pragma: no cover - Blender runtime
    raw = lib.get(TAG)
    return raw.to_dict() if hasattr(raw, "to_dict") else dict(raw or {})


def collect_facts(scene) -> dict[str, Any]:  # pragma: no cover - Blender runtime
    r = scene.render
    pct = r.resolution_percentage
    assets = []
    for lib in bpy.data.libraries:
        path = bpy.path.abspath(lib.filepath)
        assets.append({"content_id": library_identity(_tag(lib), path), "library": lib.filepath})
    return {
        "dcc": f"blender {bpy.app.version_string}",
        "file": bpy.path.basename(bpy.data.filepath) or "",
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "fps": fps_string(r.fps, r.fps_base),
        "resolution": f"{r.resolution_x * pct // 100}x{r.resolution_y * pct // 100}",
        "camera": scene.camera.name if scene.camera else "",
        "assets": assets,
    }


def _fetch(project: str, asset: str, version: str, representation: str) -> tuple[str, dict[str, Any]]:  # pragma: no cover - Blender runtime
    """Cache the version locally; returns (path, identity tag)."""
    client = make_client()
    resolved = client.resolve(build_asset_uri(project, asset, version, representation))
    cid = str(resolved["content_id"]).lower()
    path = make_cache().ensure_cached(cid, str(resolved["filename"]), size=resolved.get("size"), client=client)
    info = next(iter(client.lookup_versions([cid])), {})
    tag = {
        "content_id": cid,
        "project": project,
        "asset": asset,
        "asset_id": str(info.get("asset_id") or ""),
        "version": int(info.get("version") or 0),
        "version_id": str(info.get("version_id") or ""),
        "representation": representation,
    }
    return str(path), tag


def _top_level(collections) -> list:  # pragma: no cover - Blender runtime
    children = {c.name for col in collections for c in col.children}
    return [c for c in collections if c.name not in children]


class CHIMERA_OT_link_asset(bpy.types.Operator):
    bl_idname = "chimera.link_asset"
    bl_label = "Link Zeno Asset"
    bl_description = "Link an asset version into this file and add it to the scene"
    bl_options = {"REGISTER", "UNDO"}

    project: bpy.props.StringProperty(name="Project")
    asset: bpy.props.StringProperty(name="Asset")
    version: bpy.props.StringProperty(name="Version", default="latest")
    representation: bpy.props.StringProperty(name="Representation", default="blend")

    def execute(self, context):  # pragma: no cover - Blender runtime
        try:
            path, tag = _fetch(self.project, self.asset, self.version or "latest", self.representation or "blend")
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Could not fetch {self.asset}: {exc}")
            return {"CANCELLED"}
        before = set(bpy.data.collections)
        with bpy.data.libraries.load(path, link=True) as (src, dst):
            names = list(src.collections)
            preferred = [n for n in names if n.lower() == self.asset.lower()]
            dst.collections = preferred or names
        linked = [c for c in bpy.data.collections if c not in before and c.library is not None]
        lib = _library_for(path)
        if lib is not None:
            lib[TAG] = tag
        if not linked:
            self.report({"WARNING"}, f"{self.asset} v{tag['version']:03d} is linked but has no collections to add.")
            return {"FINISHED"}
        target = context.collection or context.scene.collection
        for col in _top_level(linked):
            inst = bpy.data.objects.new(col.name, None)
            inst.instance_type = "COLLECTION"
            inst.instance_collection = col
            inst[TAG] = {"asset": self.asset, "version": tag["version"]}
            target.objects.link(inst)
        self.report({"INFO"}, f"Linked {self.asset} v{tag['version']:03d}.")
        return {"FINISHED"}


class CHIMERA_OT_update_linked_asset(bpy.types.Operator):
    bl_idname = "chimera.update_linked_asset"
    bl_label = "Update Linked Zeno Asset"
    bl_description = "Point a linked library at another version of its asset and reload it"
    bl_options = {"REGISTER", "UNDO"}

    library: bpy.props.StringProperty(name="Library path, as stored in the file")
    project: bpy.props.StringProperty(name="Project")
    asset: bpy.props.StringProperty(name="Asset")
    version: bpy.props.StringProperty(name="Version")
    representation: bpy.props.StringProperty(name="Representation", default="blend")

    def execute(self, context):  # pragma: no cover - Blender runtime
        lib = next((lb for lb in bpy.data.libraries if lb.filepath == self.library), None)
        if lib is None:
            self.report({"ERROR"}, f"No linked library {self.library}.")
            return {"CANCELLED"}
        project = self.project or str(_tag(lib).get("project") or "")
        if not project:
            self.report({"ERROR"}, "This library has no Zeno project; link it again from Zeno.")
            return {"CANCELLED"}
        try:
            path, tag = _fetch(project, self.asset, self.version, self.representation or "blend")
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Could not fetch {self.asset} v{self.version}: {exc}")
            return {"CANCELLED"}
        lib.filepath = path
        lib.reload()
        lib[TAG] = tag
        self.report({"INFO"}, f"{self.asset} is now v{tag['version']:03d}.")
        return {"FINISHED"}


# --- linked files on another machine ----------------------------------------------------

_relinking = False


def _entries() -> list[dict[str, Any]]:  # pragma: no cover - Blender runtime
    out = []
    for lib in bpy.data.libraries:
        path = bpy.path.abspath(lib.filepath)
        out.append({"stored": lib.filepath, "abspath": path, "exists": os.path.isfile(path), "tag": _tag(lib)})
    return out


def _notify(message: str, level: str = "INFO") -> None:  # pragma: no cover - Blender runtime
    """Show a message in Blender's status bar (reports need an operator and a window)."""
    print(f"[Zeno] {message}")
    wm = bpy.context.window_manager
    window = wm.windows[0] if wm and wm.windows else None
    if window is None:
        return
    with bpy.context.temp_override(window=window, screen=window.screen):
        bpy.ops.zeno.notify(message=message, level=level)


def relink_missing(*, on_done=None) -> int:  # pragma: no cover - Blender runtime
    """Fetch missing Zeno libraries into this machine's cache and reload them.

    Returns how many libraries are being fetched (0: nothing to do). ``on_done(fixed, failed)``
    runs on the main thread when finished.
    """
    global _relinking
    todo = missing_libraries(_entries())
    if not todo or _relinking:
        return 0
    _relinking = True
    names = ", ".join(sorted({t["asset"] or t["filename"] for t in todo}))
    _notify(f"Fetching {len(todo)} linked Zeno asset(s) for this machine: {names}…")
    client, cache = make_client(), make_cache()
    fetched: dict[str, str] = {}
    failed: list[str] = []

    def work() -> None:
        for cid in {t["content_id"]: t for t in todo}:
            item = next(t for t in todo if t["content_id"] == cid)
            try:
                fetched[cid] = str(cache.ensure_cached(cid, item["filename"], size=None, client=client))
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{item['asset'] or item['filename']}: {exc}")

    thread = threading.Thread(target=work, name="zeno-relink", daemon=True)
    thread.start()

    def finish():
        global _relinking
        if thread.is_alive():
            return 0.5
        fixed = 0
        for t in todo:
            path = fetched.get(t["content_id"])
            lib = next((lb for lb in bpy.data.libraries if lb.filepath == t["stored"]), None)
            if not path or lib is None:
                continue
            try:
                lib.filepath = path
                lib.reload()
                fixed += 1
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{t['asset'] or t['filename']}: {exc}")
        _relinking = False
        if failed:
            _notify(f"Fetched {fixed} linked asset(s); {len(failed)} failed: {failed[0]}", "WARNING")
        else:
            _notify(f"Fetched {fixed} linked Zeno asset(s) into this machine's cache.")
        if on_done is not None:
            on_done(fixed, failed)
        return None

    bpy.app.timers.register(finish, first_interval=0.5)
    return len(todo)


@persistent
def _on_load(_dummy) -> None:  # pragma: no cover - Blender runtime
    prefs = addon_prefs()
    if prefs is None or getattr(prefs, "fetch_linked_on_open", True):
        # After load_post returns, so the window is ready for the status-bar message.
        bpy.app.timers.register(lambda: (relink_missing(), None)[1], first_interval=0.2)


class ZENO_OT_fix_linked_assets(bpy.types.Operator):
    bl_idname = "zeno.fix_linked_assets"
    bl_label = "Fix Linked Assets"
    bl_description = "Fetch Zeno assets this file links but this machine doesn't have yet, and reload them"

    def execute(self, context):  # pragma: no cover - Blender runtime
        if relink_missing() == 0:
            self.report({"INFO"}, "All linked Zeno assets are on this machine.")
        return {"FINISHED"}


class ZENO_OT_notify(bpy.types.Operator):
    bl_idname = "zeno.notify"
    bl_label = "Zeno Message"
    bl_options = {"INTERNAL"}

    message: bpy.props.StringProperty()
    level: bpy.props.StringProperty(default="INFO")

    def execute(self, context):  # pragma: no cover - Blender runtime
        self.report({self.level if self.level in ("INFO", "WARNING", "ERROR") else "INFO"}, self.message)
        return {"FINISHED"}


_CLASSES = (CHIMERA_OT_link_asset, CHIMERA_OT_update_linked_asset, ZENO_OT_fix_linked_assets, ZENO_OT_notify)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.app.handlers.load_post.append(_on_load)


def unregister() -> None:
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
