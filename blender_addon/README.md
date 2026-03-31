# Chimera Blender Addon

Install path:

1. In Blender: **Edit > Preferences > Add-ons > Install...**
2. Select the `chimera_zeno` addon folder as a zip (or place it under Blender scripts/addons).
3. Enable **Chimera Zeno**.

The addon expects `zeno_client` to be importable. In this repo layout, the addon bootstrap adds `zeno-plugin/` to `sys.path` so local dev works without re-packaging.

## Features

- **Load**: resolve URI, cache through `LocalCache`, open `.blend`.
- **Publish**: lock, publish current `.blend` with `publish_chunked_file(..., dcc="blender")`, release lock.
- **Command Palette** (`Ctrl+K`): search assets, load latest/explicit version, trigger publish.

## Addon preferences

- API Base URL (default: `http://127.0.0.1:8000`)
- Default project / asset
- User ID + session tracking
- Cache max size (GiB)
- Omni publish toggle

## Manual test checklist

1. Set API URL and default project (`ndfc`) in addon preferences.
2. Open **View3D > Sidebar > Chimera**, run **Load Latest** for a known asset.
3. Confirm file is cached under `~/.chimera/cache/...` and Blender opens it.
4. Save current file, run **Publish Current .blend**.
5. Verify new version row exists in API/DB and lock is released on completion.
6. Press `Ctrl+K`, search asset text, choose asset + version, execute load.
7. Use `Ctrl+K` with action `Publish` to publish selected asset context.

