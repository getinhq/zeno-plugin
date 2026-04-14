from __future__ import annotations

from pathlib import Path

import bpy

from .bridge import addon_prefs, heartbeat, make_client, publish_chunked_file


class CHIMERA_OT_publish_current_file(bpy.types.Operator):
    bl_idname = "chimera.publish_current_file"
    bl_label = "Chimera Publish Current Blend"
    bl_description = "Upload current .blend and register a new version"

    project: bpy.props.StringProperty(name="Project", default="")
    asset: bpy.props.StringProperty(name="Asset", default="")
    version: bpy.props.StringProperty(name="Version", default="next")
    representation: bpy.props.StringProperty(name="Representation", default="blend")

    def execute(self, context):  # pragma: no cover - Blender runtime
        prefs = addon_prefs()
        project = (self.project or (prefs.default_project if prefs else "")).strip()
        asset = (self.asset or (prefs.default_asset if prefs else "")).strip()
        version = (self.version or "next").strip()
        representation = (self.representation or "blend").strip()
        file_path = Path(bpy.data.filepath)
        if not project or not asset:
            self.report({"ERROR"}, "Project and Asset are required.")
            return {"CANCELLED"}
        if not file_path.exists():
            self.report({"ERROR"}, "Current .blend file must be saved first.")
            return {"CANCELLED"}

        client = make_client()
        sid = getattr(prefs, "session_id", "").strip() if prefs else ""
        uid = getattr(prefs, "user_id", "blender_user").strip() if prefs else "blender_user"
        lock_acquired = False
        try:
            heartbeat(client, project=project, asset=asset)
            if sid:
                client.acquire_lock(
                    user_id=uid,
                    session_id=sid,
                    project=project,
                    asset=asset,
                    representation=representation,
                )
                lock_acquired = True
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
            )
            vn = out.registered_version.get("version_number")
            self.report({"INFO"}, f"Published v{vn} ({out.uploaded_chunks} uploads)")
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, f"Publish failed: {e}")
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

