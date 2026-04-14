from __future__ import annotations

import json
from pathlib import Path

from maya.canonicalize import canonicalize
from zeno_client.chunking import ChunkingConfig
from zeno_client.omni_ingest import ingest_omni_file


class _MemClient:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def blob_exists(self, h: str) -> bool:
        return h in self.store

    def upload_blob_bytes(self, body: bytes, h: str) -> None:
        self.store[h] = body

    def get_blob_bytes(self, h: str) -> bytes:
        return self.store[h]


def _ma_scene(anim_tail: str) -> bytes:
    return f"""//Maya ASCII 2024 scene
requires maya "2024";
createNode animCurveTL -n "animCurveTL1";
setAttr ".ktv[0:3]" 1 0.0000001 2 1.0000002 3 2.0000003 4 {anim_tail};
createNode mesh -n "meshShape1";
setAttr ".vt" -type "pointArray" 4 1.49999998 2.124000001 3.0 1.0 0.0 1.0 0.0 1.0 -1.0 0.5 0.2 1.0 0.0 0.0 0.0 1.0;
""".encode("utf-8")


def _segment_hashes(manifest_bytes: bytes) -> set[str]:
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    return {
        str(seg.get("hash"))
        for seg in (manifest.get("segments") or [])
        if str(seg.get("kind") or "").lower() == "raw_chunk"
    }


def test_maya_omni_reuses_chunks_for_small_anim_change(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CHIMERA_MA_FLOAT_QUANTIZE", "1")
    monkeypatch.setenv("CHIMERA_MA_FORCED_CUTS", "1")

    v1 = tmp_path / "v1.ma"
    v2 = tmp_path / "v2.ma"
    v1.write_bytes(_ma_scene("3.0000004"))
    v2.write_bytes(_ma_scene("3.5000004"))

    c1 = canonicalize(v1.read_bytes())
    c2 = canonicalize(v2.read_bytes())

    client = _MemClient()
    r1 = ingest_omni_file(
        client=client,
        path=v1,
        filename=v1.name,
        chunking=ChunkingConfig(avg=256, min=128, max=1024),
        canonical_bytes=c1,
        dcc="maya",
    )
    r2 = ingest_omni_file(
        client=client,
        path=v2,
        filename=v2.name,
        chunking=ChunkingConfig(avg=256, min=128, max=1024),
        canonical_bytes=c2,
        dcc="maya",
    )

    h1 = _segment_hashes(r1.manifest_bytes)
    h2 = _segment_hashes(r2.manifest_bytes)
    assert h1.intersection(h2)
