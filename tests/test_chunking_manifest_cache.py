import os
from pathlib import Path

import httpx
from blake3 import blake3

from zeno_client import CacheConfig, LocalCache, ZenoClient
from zeno_client.chunking import ChunkingConfig, chunk_file
from zeno_client.manifest import build_manifest_v2, manifest_blake3


def _transport(handler):
    def _h(req: httpx.Request) -> httpx.Response:
        return handler(req)

    return httpx.MockTransport(_h)


def test_chunker_stability_small_edit(tmp_path: Path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    base = os.urandom(256 * 1024)  # 256KB random payload
    a.write_bytes(base)
    # insert a small edit near the front
    b.write_bytes(base[:200] + b"XX" + base[200:])

    cfg = ChunkingConfig(avg=16 * 1024, min=4 * 1024, max=64 * 1024)
    ca = chunk_file(a, cfg=cfg)
    cb = chunk_file(b, cfg=cfg)

    # Ignore the first couple chunks (edit near front shifts early boundaries); expect later chunks to overlap.
    sa = {c.content_hash for c in ca[2:]}
    sb = {c.content_hash for c in cb[2:]}
    assert len(sa.intersection(sb)) >= 1


def test_cache_manifest_reassembles(tmp_path: Path):
    # Build a small fake file, chunk it, create manifest, and simulate CAS server by serving blob bytes for hashes.
    data = b"hello" * 10000
    whole = blake3(data).hexdigest()

    f = tmp_path / "file.bin"
    f.write_bytes(data)

    cfg = ChunkingConfig(avg=4096, min=1024, max=16384)
    chunks = chunk_file(f, cfg=cfg)
    manifest_bytes = build_manifest_v2(
        filename="file.bin",
        size_bytes=len(data),
        whole_file_blake3=whole,
        chunking=cfg,
        chunks=chunks,
    )
    mid = manifest_blake3(manifest_bytes)

    # CAS store: key->bytes
    store = {mid: manifest_bytes}
    # add chunk blobs
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
    assert out.exists()
    assert out.read_bytes() == data
    assert blake3(out.read_bytes()).hexdigest() == whole

