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
from zeno_client import CacheConfig, LocalCache, ZenoClient, compute_blake3
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
h = compute_blake3(p)
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
- `blender/chimera_zeno/` — Blender addon (load/publish + command palette with Ctrl+K).
- `maya/`, `houdini/`, `nuke/`, `blender/` — DCC-specific helpers (`blender/` also hosts `chimera_zeno`).

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

- **Chunks**: each chunk is a CAS blob keyed by BLAKE3 of the chunk bytes.
- **Manifest**: a small JSON blob (`schema=chimera.manifest.v2`) stored in CAS; it lists chunk hashes in order and includes the **whole-file BLAKE3** for integrity.
- **Version registration**: `content_id` stored in the versions DB becomes the **manifest hash**.

### Omni-Chunker mode (`chimera.manifest.v3`)

Enable via env:

```bash
export OMNI_CHUNKER=1
```

When enabled, publisher uses entropy-aware ingest:

- Low/mid entropy regions -> CDC `raw_chunk` segments.
- High entropy regions -> optional `zstd_dict_patch` segments (when parent bytes are available and `zstandard` is installed).
- Manifest schema: `chimera.manifest.v3` with ordered `segments`.

`LocalCache` can materialize v3 manifests with:

- `raw_chunk` replay
- `zstd_dict_patch` replay (requires `zstandard`)

### Dual-artifact mode (DCC canonical + Omni, e.g. Blender)

When `publish_chunked_file(..., use_omni=True, dcc="blender")` and a DCC canonicalizer runs:

- **Delivery artifact**: the original file bytes are uploaded as a single CAS blob; **register-version** uses that blob’s BLAKE3 as `content_id` (resolver + `LocalCache` load the byte-identical source file).
- **Dedup artifact**: the Omni `chimera.manifest.v3` manifest (and chunk/patch blobs) remains in CAS; its hash is stored under `versions.metadata.dedup_artifact` (`content_id`, `schema`, `dcc`, `dcc_canonical`).
- **Parent resolution for v2+**: the publisher calls `latest_content_id(..., artifact="dedup")` so Omni patching references the previous **manifest** id, not the raw delivery hash.
- **`PublishChunkedResult`**: `delivery_content_id` / `dedup_manifest_id` expose both; `manifest_id` remains the dedup manifest id for backward compatibility.

Versions without `metadata.dedup_artifact` behave as before (single manifest `content_id`).

Cache behavior:

- When resolving a version, `LocalCache` will:
  - treat `content_id` as a **manifest blob** if it parses as `chimera.manifest.v1`
  - download any missing chunks into `~/.chimera/cache/chunks/<prefix>/<hash>`
  - assemble into `~/.chimera/cache/<whole_file_blake3>/<filename>`
  - verify assembled file BLAKE3 equals `whole_file_blake3`

## Maya ASCII canonicalization flags

Maya `.ma` canonicalization supports staged hardening flags:

- `CHIMERA_MA_FLOAT_QUANTIZE=1`
  - Enables precision-safe float quantization for dense numeric `setAttr` payloads.
- `CHIMERA_MA_FLOAT_DECIMALS=5`
  - Decimal precision for quantization (used when `CHIMERA_MA_FLOAT_QUANTIZE=1`).
- `CHIMERA_MA_PLUGIN_PAYLOAD_NORMALIZE=1`
  - Neutralizes known volatile plugin payload storage strings (allowlisted types only).
- `CHIMERA_MA_FORCED_CUTS=1`
  - Enables Maya semantic anchor-guided chunk boundaries in Omni ingest.

Recommended rollout:

1. Enable quantization + plugin payload normalization in dev/staging.
2. Validate opens/publishes for representative rigs/animation scenes.
3. Enable forced cuts in staging and compare dedup metrics.

### Maya dedup benchmark helper

Use the benchmark script to compare two `.ma` versions with canonicalization + CDC:

```bash
python scripts/ma_dedup_benchmark.py \
  --left /path/to/v001.ma \
  --right /path/to/v002.ma \
  --avg 1048576 --min 262144 --max 4194304
```

## CDC benchmark harness

Run a chunk stability benchmark with real + synthetic mutations:

```bash
python scripts/cdc_benchmark.py \
  --left "/Users/osho/Desktop/snow_v4.3.blend" \
  --right "/Users/osho/Desktop/snow_v4.4.blend" \
  --synthetic-count 12 \
  --output /tmp/chimera_cdc_report.json
```

Inspecting:

- **Manifests** saved to: `~/.chimera/cache/manifests/<manifest_hash>.json`
- **Chunks** saved to: `~/.chimera/cache/chunks/<hash_prefix>/<hash>`

## Tech

Python 3.11+. Stack: see [zeno-api docs/DECISION_LOG.md](https://github.com/your-org/zeno-api/blob/main/docs/DECISION_LOG.md).

## Rust + PyO3 (optional path)

If you choose Rust-first entropy/chunking:

- Use `maturin` to build wheels.
- Ship prebuilt wheels for macOS (arm64 + x86_64), Linux, and Windows.
- Keep a narrow Python API boundary (scan/chunk functions), and keep manifest/publisher orchestration in Python for fast iteration.
