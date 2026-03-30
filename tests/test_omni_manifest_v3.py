from pathlib import Path

import httpx
from blake3 import blake3

from zeno_client import CacheConfig, LocalCache, ZenoClient
from zeno_client.chunking import ChunkingConfig, chunk_file
from zeno_client.entropy_segment import EntropyConfig, scan_entropy_segments
from zeno_client.manifest import build_manifest_v3, parse_manifest
from zeno_client.omni_ingest import _zstd_training_samples, materialize_from_manifest_v3

try:
    import zstandard as zstd
except Exception:  # pragma: no cover
    zstd = None


def _transport(handler):
    def _h(req: httpx.Request) -> httpx.Response:
        return handler(req)

    return httpx.MockTransport(_h)


def test_entropy_scanner_segments(tmp_path: Path):
    f = tmp_path / "entropy.bin"
    f.write_bytes((b"\x00" * 65536) + bytes(range(256)) * 256)
    segs = scan_entropy_segments(f, EntropyConfig(window_size=4096, low_threshold=1.0, high_threshold=7.0))
    assert len(segs) >= 2
    modes = {s.mode for s in segs}
    assert "low" in modes


def test_manifest_v3_roundtrip_raw_chunks(tmp_path: Path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello world " * 1000)
    chunks = chunk_file(p, ChunkingConfig(avg=2048, min=1024, max=8192))
    segments = [{"kind": "raw_chunk", "hash": c.content_hash, "size": c.size} for c in chunks]
    mbytes = build_manifest_v3(
        filename=p.name,
        size_bytes=p.stat().st_size,
        whole_file_blake3=blake3(p.read_bytes()).hexdigest(),
        chunking=ChunkingConfig(avg=2048, min=1024, max=8192),
        segments=segments,
    )
    m = parse_manifest(mbytes)
    assert m.schema == "chimera.manifest.v3"
    assert len(m.chunks) == len(chunks)
    assert m.segments is not None


def test_materialize_from_manifest_v3_patch(tmp_path: Path):
    if zstd is None:
        return
    base = b"A" * 4096 + b"XYZ" * 1000
    new = b"A" * 4096 + b"XY1" * 1000
    dict_obj = zstd.train_dictionary(4096, _zstd_training_samples(base))
    dict_bytes = dict_obj.as_bytes()
    dctx = zstd.ZstdCompressor(dict_data=dict_obj)
    patch = dctx.compress(new)
    dict_hash = blake3(dict_bytes).hexdigest()
    patch_hash = blake3(patch).hexdigest()
    manifest = {
        "schema": "chimera.manifest.v3",
        "filename": "x.bin",
        "segments": [
            {
                "kind": "zstd_dict_patch",
                "parent_content_id": "0" * 64,
                "range_start": 0,
                "range_end": len(new),
                "dict_hash": dict_hash,
                "patch_hash": patch_hash,
                "patch_size": len(patch),
                "uncompressed_size": len(new),
            }
        ],
    }
    store = {dict_hash: dict_bytes, patch_hash: patch}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.startswith("/api/v1/cas/blobs/"):
            h = req.url.path.split("/")[-1]
            if h in store:
                return httpx.Response(200, content=store[h])
            return httpx.Response(404, json={"detail": "missing"})
        return httpx.Response(404, json={"detail": "nope"})

    client = ZenoClient(base_url="http://api", transport=_transport(handler))
    out = materialize_from_manifest_v3(manifest=manifest, client=client, out_path=tmp_path / "out.bin")
    assert out.read_bytes() == new


def test_local_cache_reads_manifest_v3_raw(tmp_path: Path):
    data = b"abc123" * 8000
    f = tmp_path / "file.bin"
    f.write_bytes(data)
    cfg = ChunkingConfig(avg=4096, min=1024, max=16384)
    chunks = chunk_file(f, cfg=cfg)
    segments = [{"kind": "raw_chunk", "hash": c.content_hash, "size": c.size} for c in chunks]
    manifest_bytes = build_manifest_v3(
        filename="file.bin",
        size_bytes=len(data),
        whole_file_blake3=blake3(data).hexdigest(),
        chunking=cfg,
        segments=segments,
    )
    mid = blake3(manifest_bytes).hexdigest()
    store = {mid: manifest_bytes}
    with f.open("rb") as r:
        for ch in chunks:
            r.seek(ch.offset)
            store[ch.content_hash] = r.read(ch.size)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/resolve":
            return httpx.Response(200, json={"content_id": mid, "filename": "file.bin", "size": len(data)})
        if req.url.path.startswith("/api/v1/cas/blobs/") and req.url.path.endswith("/exists"):
            h = req.url.path.split("/")[-2]
            return httpx.Response(200, json={"exists": True}) if h in store else httpx.Response(404, json={"detail": "Blob not found"})
        if req.url.path.startswith("/api/v1/cas/blobs/"):
            h = req.url.path.split("/")[-1]
            if h in store:
                return httpx.Response(200, content=store[h])
            return httpx.Response(404, json={"detail": "Blob not found"})
        return httpx.Response(404, json={"detail": "nope"})

    c = ZenoClient(base_url="http://api", transport=_transport(handler))
    cache = LocalCache(CacheConfig(root_dir=tmp_path / "cache", max_bytes=10**9))
    out = cache.ensure_uri_cached("asset://P/A/latest/bin", client=c)
    assert out.read_bytes() == data
