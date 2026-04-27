from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

import bpy

from .bridge import addon_prefs, heartbeat, make_client, publish_chunked_file
from . import hub_bridge


# Shared state: the Qt palette's worker thread reads this after a queued
# publish operator call completes so it can surface the real version
# number / upload count to the artist instead of a generic "success".
# Cleared at the start of every execute() to avoid stale reads if two
# publishes fire back to back.
_LAST_PUBLISH_RESULT: dict[str, Any] = {}

# Valid stages must stay in lockstep with the API check in
# ``zeno-api/app/versions/service.py``. Empty string = legacy/global.
_VALID_STAGES = frozenset({"", "modelling", "texturing", "rigging", "lookdev"})


def _log(level: str, message: str) -> None:
    """Print a tagged line to stdout so it shows up in the terminal Blender
    was launched from (or ``Window → Toggle System Console`` on Windows).

    ``self.report`` alone is not enough — users rarely have the Info area
    visible, which is exactly why the previous "nothing happens, no logs"
    bug was hard to diagnose.
    """
    print(f"[chimera.publish] {level}: {message}", flush=True)


def _report(operator, level: str, message: str) -> None:
    _log(level, message)
    try:
        operator.report({level}, message)
    except Exception:
        pass


class CHIMERA_OT_publish_current_file(bpy.types.Operator):
    bl_idname = "chimera.publish_current_file"
    bl_label = "Chimera Publish Current Blend"
    bl_description = "Upload current .blend and register a new version"

    project: bpy.props.StringProperty(name="Project", default="")
    asset: bpy.props.StringProperty(name="Asset", default="")
    version: bpy.props.StringProperty(name="Version", default="next")
    representation: bpy.props.StringProperty(name="Representation", default="blend")
    pipeline_stage: bpy.props.StringProperty(
        name="Pipeline Stage",
        description=(
            "Asset pipeline stage: modelling, texturing, rigging, or lookdev. "
            "Leave empty to publish into the legacy/global bucket — but then "
            "the new version will not appear in any stage-specific feed on "
            "the dashboard."
        ),
        default="",
    )
    task_id: bpy.props.StringProperty(
        name="Task ID",
        description="Task UUID linked to this publish",
        default="",
    )

    def execute(self, context):  # pragma: no cover - Blender runtime
        _LAST_PUBLISH_RESULT.clear()

        prefs = addon_prefs()
        project = (self.project or (prefs.default_project if prefs else "")).strip()
        asset = (self.asset or (prefs.default_asset if prefs else "")).strip()
        version = (self.version or "next").strip()
        representation = (self.representation or "blend").strip()
        stage = (self.pipeline_stage or "").strip().lower()
        task_id = (self.task_id or "").strip() or None
        file_path = Path(bpy.data.filepath or "")
        _log(
            "INFO",
            f"execute start project={project!r} asset={asset!r} "
            f"version={version!r} rep={representation!r} stage={stage!r} "
            f"task_id={task_id!r} file={str(file_path)!r}",
        )
        if not project or not asset:
            _report(self, "ERROR", "Project and Asset are required.")
            _LAST_PUBLISH_RESULT.update({"ok": False, "message": "Project and Asset are required."})
            return {"CANCELLED"}
        if stage not in _VALID_STAGES:
            valid = sorted(s for s in _VALID_STAGES if s) or ["<empty>"]
            _report(
                self,
                "ERROR",
                f"Invalid pipeline_stage={stage!r}. Allowed: {', '.join(valid)} or empty.",
            )
            _LAST_PUBLISH_RESULT.update(
                {"ok": False, "message": f"Invalid pipeline_stage={stage!r}."}
            )
            return {"CANCELLED"}
        if str(file_path) == "" or not file_path.exists():
            _report(
                self,
                "ERROR",
                "Current .blend file must be saved first "
                f"(bpy.data.filepath={bpy.data.filepath!r}).",
            )
            _LAST_PUBLISH_RESULT.update(
                {"ok": False, "message": "Save the .blend file before publishing."}
            )
            return {"CANCELLED"}

        if hub_bridge.hub_enabled():
            _log("INFO", "hub mode enabled — routing publish via chimera_hub")
            hub = hub_bridge.get_hub_client()
            if hub is None:
                _log("WARNING", "hub client unavailable; falling back to in-process.")
            else:
                try:
                    resp = hub.publish(
                        path=file_path,
                        project=project,
                        asset=asset,
                        representation=representation,
                        version=version,
                        dcc="blender",
                        pipeline_stage=stage,
                        task_id=task_id or "",
                    )
                    _log("INFO", f"hub publish response: {resp!r}")
                    if resp.get("ok"):
                        vn = resp.get("version")
                        stage_label = stage or "legacy"
                        _report(
                            self,
                            "INFO",
                            f"Published v{vn} ({stage_label}) via hub",
                        )
                        _LAST_PUBLISH_RESULT.update(
                            {
                                "ok": True,
                                "version": vn,
                                "pipeline_stage": stage,
                                "message": f"Published v{vn} to {stage_label} stage via hub.",
                            }
                        )
                        return {"FINISHED"}
                    _report(self, "ERROR", f"Hub publish failed: {resp.get('message')}")
                    _LAST_PUBLISH_RESULT.update(
                        {"ok": False, "message": str(resp.get("message") or "Hub publish failed.")}
                    )
                    return {"CANCELLED"}
                except Exception as exc:
                    traceback.print_exc()
                    _report(
                        self,
                        "WARNING",
                        f"Hub publish error: {exc}; falling back to in-process.",
                    )

        prefs_url = getattr(prefs, "api_base_url", "") if prefs else ""
        _log("INFO", f"in-process publish via API base_url={prefs_url!r}")

        try:
            client = make_client()
        except Exception as exc:
            traceback.print_exc()
            _report(self, "ERROR", f"Could not build API client: {exc}")
            _LAST_PUBLISH_RESULT.update(
                {"ok": False, "message": f"Could not build API client: {exc}"}
            )
            return {"CANCELLED"}

        sid = getattr(prefs, "session_id", "").strip() if prefs else ""
        uid = getattr(prefs, "user_id", "blender_user").strip() if prefs else "blender_user"
        lock_acquired = False
        try:
            _log("INFO", "heartbeat → API")
            heartbeat(client, project=project, asset=asset)
            if sid:
                _log("INFO", f"acquire_lock sid={sid!r} uid={uid!r}")
                client.acquire_lock(
                    user_id=uid,
                    session_id=sid,
                    project=project,
                    asset=asset,
                    representation=representation,
                )
                lock_acquired = True
            _log("INFO", "publish_chunked_file → upload + register")
            out = publish_chunked_file(
                client=client,
                project=project,
                asset=asset,
                representation=representation,
                path=file_path,
                version=version,
                filename=file_path.name,
                use_omni=bool(prefs.use_omni_publish) if prefs else True,
                dcc="blender",
                pipeline_stage=stage,
                task_id=task_id,
            )
            vn = out.registered_version.get("version_number")
            stage_label = stage or "legacy"
            _report(
                self,
                "INFO",
                f"Published v{vn} ({stage_label}, {out.uploaded_chunks} uploads)",
            )
            _LAST_PUBLISH_RESULT.update(
                {
                    "ok": True,
                    "version": vn,
                    "uploaded_chunks": out.uploaded_chunks,
                    "manifest_id": out.manifest_id,
                    "pipeline_stage": stage,
                    "task_id": task_id,
                    "message": f"Published v{vn} to {stage_label} stage "
                    f"({out.uploaded_chunks} chunks uploaded).",
                }
            )
            return {"FINISHED"}
        except Exception as e:
            traceback.print_exc()
            _report(self, "ERROR", f"Publish failed: {e}")
            _LAST_PUBLISH_RESULT.update({"ok": False, "message": f"Publish failed: {e}"})
            return {"CANCELLED"}
        finally:
            if lock_acquired and sid:
                try:
                    client.release_lock(
                        user_id=uid,
                        session_id=sid,
                        project=project,
                        asset=asset,
                        representation=representation,
                    )
                except Exception:
                    pass


def register() -> None:
    bpy.utils.register_class(CHIMERA_OT_publish_current_file)


def unregister() -> None:
    bpy.utils.unregister_class(CHIMERA_OT_publish_current_file)

