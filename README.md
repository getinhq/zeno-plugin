# zeno-plugin

Zeno DCC plugins and shared Python client. Used by Maya, Houdini, Nuke, and Blender for resolve, upload, register-version, and session.

## Layout

- `zeno_client/` — shared library (resolve, upload_blob, register_version, session). Used by all plugins.
- `maya/`, `houdini/`, `nuke/`, `blender/` — plugin placeholders (no implementation yet).

## Tech

Python 3.11+. Stack: see [zeno-api docs/DECISION_LOG.md](https://github.com/your-org/zeno-api/blob/main/docs/DECISION_LOG.md).
