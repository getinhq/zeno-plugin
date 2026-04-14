from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from zeno_client.chunking import ChunkingConfig
from zeno_client.dcc_registry import canonicalize_file
from zeno_client.omni_ingest import ingest_omni_file, materialize_from_manifest_v3


class _MemClient:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def blob_exists(self, h: str) -> bool:
        return h in self.store

    def upload_blob_bytes(self, body: bytes, h: str) -> None:
        self.store[h] = body

    def get_blob_bytes(self, h: str) -> bytes:
        return self.store[h]


def _build_usdz(entries: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as zf:
        for name, payload in entries:
            zf.writestr(name, payload)
    return out.getvalue()


def test_omni_roundtrip_usda_canonical(tmp_path: Path):
    src = tmp_path / "a.usda"
    src.write_bytes(b"#usda 1.0\n(\n)\n")

    canonical = canonicalize_file(src, dcc_hint="usd")
    assert canonical is not None

    client = _MemClient()
    res = ingest_omni_file(
        client=client,
        path=src,
        filename=src.name,
        chunking=ChunkingConfig(avg=1024, min=512, max=4096),
        canonical_bytes=canonical,
        dcc="usd",
    )
    manifest = json.loads(res.manifest_bytes.decode("utf-8"))
    out = materialize_from_manifest_v3(manifest=manifest, client=client, out_path=tmp_path / "out.usda")
    assert out.read_bytes() == canonical


def test_omni_roundtrip_usdz_repacked(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CHIMERA_USDZ_REPACK", "1")
    src = tmp_path / "a.usdz"
    src.write_bytes(
        _build_usdz(
            [
                ("b.usdc", b"PXR-USDC\x00v2"),
                ("a.usda", b"#usda 1.0\n"),
            ]
        )
    )

    canonical = canonicalize_file(src, dcc_hint="usd")
    assert canonical is not None
    assert canonical != src.read_bytes()

    client = _MemClient()
    res = ingest_omni_file(
        client=client,
        path=src,
        filename=src.name,
        chunking=ChunkingConfig(avg=1024, min=512, max=4096),
        canonical_bytes=canonical,
        dcc="usd",
    )
    manifest = json.loads(res.manifest_bytes.decode("utf-8"))
    out = materialize_from_manifest_v3(manifest=manifest, client=client, out_path=tmp_path / "out.usdz")
    assert out.read_bytes() == canonical
