"""Publishing follows the studio's storage workflow (chunked or whole file per extension)."""
from pathlib import Path
from unittest.mock import MagicMock

from blake3 import blake3

from zeno_client.publisher import publish_chunked_file, resolve_strategy


def _client(decision):
    client = MagicMock()
    if isinstance(decision, Exception):
        client.resolve_storage.side_effect = decision
    else:
        client.resolve_storage.return_value = decision
    client.blob_exists.return_value = False
    client.latest_content_id.return_value = None
    registered = {}

    def register_version(**kw):
        registered.update(kw)
        return {"version_number": 1, **kw}

    client.register_version = register_version
    return client, registered


def test_whole_strategy_uploads_the_file_as_one_blob(tmp_path: Path, monkeypatch):
    f = tmp_path / "shot.fbx"
    f.write_bytes(b"FBX" * 1000)
    client, registered = _client({"strategy": "whole", "reason": "Studio rule for .fbx.", "algorithm_version": "2026.10"})
    monkeypatch.setattr("zeno_client.publisher.ingest_omni_file", lambda **kw: (_ for _ in ()).throw(AssertionError("chunked")))
    res = publish_chunked_file(client=client, project="P", asset="A", representation="fbx", path=f, use_omni=True)
    whole = blake3(f.read_bytes()).hexdigest()
    assert res.strategy == "whole" and res.manifest_id == whole and res.uploaded_chunks == 1
    client.upload_blob.assert_called_once_with(f, whole)
    assert registered["content_id"] == whole
    assert registered["metadata"]["storage"] == {"strategy": "whole", "reason": "Studio rule for .fbx.", "algorithm_version": "2026.10"}
    client.resolve_storage.assert_called_once_with("shot.fbx")


def test_whole_strategy_skips_upload_when_stored(tmp_path: Path):
    f = tmp_path / "shot.fbx"
    f.write_bytes(b"same")
    client, _ = _client({"strategy": "whole"})
    client.blob_exists.return_value = True
    res = publish_chunked_file(client=client, project="P", asset="A", representation="fbx", path=f, use_omni=False)
    assert res.uploaded_chunks == 0 and not client.upload_blob.called


def test_chunked_strategy_records_itself(tmp_path: Path):
    f = tmp_path / "scene.obj"
    f.write_bytes(b"v 0 0 0\n" * 50)
    client, registered = _client({"strategy": "chunked", "reason": "Recommended for .obj (experimental)."})
    res = publish_chunked_file(client=client, project="P", asset="A", representation="obj", path=f, use_omni=False)
    assert res.strategy == "chunked" and res.chunk_count >= 1
    assert registered["metadata"]["storage"]["strategy"] == "chunked"
    assert registered["content_id"] == res.manifest_id != blake3(f.read_bytes()).hexdigest()


def test_policy_unavailable_falls_back_to_chunked():
    client, _ = _client(RuntimeError("404 Not Found"))
    strategy, reason, _ = resolve_strategy(client, "a.blend")
    assert strategy == "chunked" and "unavailable" in reason
    client2, _ = _client({"strategy": "per_frame"})  # not a publish strategy
    assert resolve_strategy(client2, "a.exr")[0] == "chunked"


def test_caller_can_force_a_strategy(tmp_path: Path):
    f = tmp_path / "a.blend"
    f.write_bytes(b"BLENDER" * 10)
    client, _ = _client({"strategy": "chunked"})
    res = publish_chunked_file(client=client, project="P", asset="A", representation="blend", path=f, strategy="whole")
    assert res.strategy == "whole" and not client.resolve_storage.called
