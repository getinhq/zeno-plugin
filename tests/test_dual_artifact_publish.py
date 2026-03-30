"""Dual-artifact publish: raw delivery CAS id + dedup manifest in versions.metadata."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from blake3 import blake3

from zeno_client.omni_ingest import OmniIngestResult
from zeno_client.publisher import publish_chunked_file


def test_omni_with_dcc_registers_raw_delivery_and_dedup_metadata(monkeypatch, tmp_path: Path) -> None:
    """When DCC canonical bytes are used, version row points at raw file hash; dedup manifest is in metadata."""
    blend = tmp_path / "t.blend"
    raw_bytes = b"BLENDER" + b"x" * 200
    blend.write_bytes(raw_bytes)
    raw_hash = blake3(raw_bytes).hexdigest()

    omni = OmniIngestResult(
        manifest_id="a" * 64,
        manifest_bytes=b"{}",
        whole_file_blake3="b" * 64,
        chunks=[],
        uploaded_chunks=0,
        uploaded_aux_blobs=0,
        segments=[{"kind": "raw_chunk", "hash": "c" * 64, "size": 10}],
        dcc_canonical=True,
    )

    def fake_ingest(**kwargs):
        assert kwargs["canonical_bytes"] is not None
        return omni

    captured: dict = {}

    def fake_register_version(**kwargs):
        captured.update(kwargs)
        return {
            "version_id": "vid",
            "version_number": 1,
            "content_id": kwargs["content_id"],
            "filename": kwargs.get("filename"),
            "size": kwargs.get("size"),
            "metadata": kwargs.get("metadata"),
        }

    client = MagicMock()
    client.latest_content_id.return_value = None
    client.blob_exists.return_value = False
    client.upload_blob = MagicMock(return_value=True)
    client.register_version = fake_register_version

    monkeypatch.setattr("zeno_client.publisher.ingest_omni_file", fake_ingest)
    monkeypatch.setattr("zeno_client.publisher.canonicalize_file", lambda path, dcc_hint=None: b"canonical-bytes")

    res = publish_chunked_file(
        client=client,
        project="P1",
        asset="A1",
        representation="blend",
        path=blend,
        version="next",
        use_omni=True,
        dcc="blender",
    )

    assert captured["content_id"] == raw_hash
    assert captured["metadata"]["dedup_artifact"]["content_id"] == omni.manifest_id
    assert captured["metadata"]["dedup_artifact"]["schema"] == "chimera.manifest.v3"
    assert res.delivery_content_id == raw_hash
    assert res.dedup_manifest_id == omni.manifest_id
    assert res.manifest_id == omni.manifest_id
    assert client.upload_blob.call_count == 1
    assert len(captured) > 0


def test_omni_without_canonical_still_registers_manifest_only(monkeypatch, tmp_path: Path) -> None:
    """No DCC canonicalizer → single manifest content_id (legacy behavior)."""
    f = tmp_path / "plain.bin"
    f.write_bytes(b"hello world blob")

    omni = OmniIngestResult(
        manifest_id="d" * 64,
        manifest_bytes=b"{}",
        whole_file_blake3="e" * 64,
        chunks=[],
        uploaded_chunks=0,
        uploaded_aux_blobs=0,
        segments=[],
        dcc_canonical=False,
    )

    monkeypatch.setattr("zeno_client.publisher.ingest_omni_file", lambda **kw: omni)
    monkeypatch.setattr("zeno_client.publisher.canonicalize_file", lambda path, dcc_hint=None: None)

    client = MagicMock()
    client.latest_content_id.return_value = None
    captured = {}

    def fake_register_version(**kwargs):
        captured.update(kwargs)
        return {"version_id": "v", "version_number": 1, "content_id": kwargs["content_id"]}

    client.register_version = fake_register_version

    res = publish_chunked_file(
        client=client,
        project="P1",
        asset="A1",
        representation="bin",
        path=f,
        use_omni=True,
    )

    assert captured["content_id"] == omni.manifest_id
    assert captured.get("metadata") is None
    assert res.delivery_content_id is None
    assert res.dedup_manifest_id is None
