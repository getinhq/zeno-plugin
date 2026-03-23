# zeno-plugin

Zeno DCC plugins and shared Python client. Used by Maya, Houdini, Nuke, and Blender for resolve, upload, register-version, and session.

## Quickstart

Install (editable for dev):

```bash
cd zeno-plugin
pip install -e .[test]
```

Point it at your running API:

```bash
export ZENO_API_BASE_URL="http://127.0.0.1:8000"
```

Example usage:

```python
from pathlib import Path
from zeno_client import CacheConfig, LocalCache, ZenoClient, compute_sha256
from zeno_client.publisher import publish_chunked_file

c = ZenoClient()

# Resolver
r = c.resolve("asset://MS01/hero_model/latest/fbx")
print(r["content_id"], r["filename"], r["size"])

# Local cache (materialize resolver output to disk)
cache = LocalCache(
    CacheConfig(
        # default: ~/.chimera/cache
        # root_dir=Path("~/.chimera/cache").expanduser(),
        max_bytes=50 * 1024 * 1024 * 1024,  # 50GiB
    )
)
local_path = cache.ensure_uri_cached("asset://MS01/hero_model/latest/fbx", client=c)
print("Cached at:", local_path)

# Upload + Register-Version
p = Path("/path/to/file.fbx")
h = compute_sha256(p)
created = c.upload_blob(p, h)
v = c.register_version(
    project="MS01",
    asset="hero_model",
    representation="fbx",
    version="next",
    content_id=h,
    filename=p.name,
    size=p.stat().st_size,
)
print(created, v["version_number"])

# Chunked publish (CDC + manifest)
res = publish_chunked_file(
    client=c,
    project="MS01",
    asset="hero_model",
    representation="fbx",
    path=p,
    version="next",
)
print("manifest:", res.manifest_id, "chunks:", res.chunk_count, "uploaded:", res.uploaded_chunks)

# Presence heartbeat (session)
c.heartbeat(user_id="artist_01", session_id="dcc-session-uuid", metadata={"dcc": "maya"})
```

## Layout

- `zeno_client/` — shared library (resolve, upload_blob, register_version, session). Used by all plugins.
- `maya/`, `houdini/`, `nuke/`, `blender/` — plugin placeholders (no implementation yet).

## Local cache (1.2)

The cache layer materializes CAS blobs to a stable local path:

- **Default root**: `~/.chimera/cache`
- **Layout**: `~/.chimera/cache/<content_id>/<filename>`
- **Index**: `~/.chimera/cache/index.sqlite3` (LRU manifest)
- **Eviction**: size-capped LRU (delete least-recently-used entries until under cap)
- **Safety**: per-content lock + atomic rename after verifying size/hash

Primary API:

- `LocalCache.ensure_uri_cached(uri, client=ZenoClient()) -> Path`
- `LocalCache.ensure_cached(content_id, filename, size=..., client=...) -> Path`

## Chunked publish + manifest cache (chunking workflow)

This repo supports **resumable + deduplicated uploads** by splitting a file into **content-defined chunks** and uploading only missing chunk blobs to CAS.

- **Chunks**: each chunk is a CAS blob keyed by SHA-256 of the chunk bytes.
- **Manifest**: a small JSON blob (`schema=chimera.manifest.v1`) stored in CAS; it lists chunk hashes in order and includes the **whole-file SHA-256** for integrity.
- **Version registration**: `content_id` stored in the versions DB becomes the **manifest hash**.

Cache behavior:

- When resolving a version, `LocalCache` will:
  - treat `content_id` as a **manifest blob** if it parses as `chimera.manifest.v1`
  - download any missing chunks into `~/.chimera/cache/chunks/<prefix>/<hash>`
  - assemble into `~/.chimera/cache/<whole_file_sha256>/<filename>`
  - verify assembled file SHA-256 equals `whole_file_sha256`

Inspecting:

- **Manifests** saved to: `~/.chimera/cache/manifests/<manifest_hash>.json`
- **Chunks** saved to: `~/.chimera/cache/chunks/<hash_prefix>/<hash>`

## Tech

Python 3.11+. Stack: see [zeno-api docs/DECISION_LOG.md](https://github.com/your-org/zeno-api/blob/main/docs/DECISION_LOG.md).
