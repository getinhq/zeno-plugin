import json

import httpx

from zeno_client import (
    BlobNotFound,
    ContentHashMismatch,
    LockHeldByOther,
    RegisterContentNotFound,
    RegisterVersionConflict,
    ResolveBadRequest,
    ResolveNotFound,
    ZenoClient,
)


def _transport(handler):
    def _h(req: httpx.Request) -> httpx.Response:
        return handler(req)

    return httpx.MockTransport(_h)


def test_resolve_ok():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET"
        assert req.url.path == "/api/v1/resolve"
        return httpx.Response(200, json={"content_id": "h", "filename": "f", "size": 1})

    c = ZenoClient(base_url="http://api", transport=_transport(handler))
    out = c.resolve("asset://P/A/latest/fbx")
    assert out["content_id"] == "h"


def test_resolve_400_maps():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "bad uri"})

    c = ZenoClient(base_url="http://api", transport=_transport(handler))
    try:
        c.resolve("nope")
        assert False
    except ResolveBadRequest:
        assert True


def test_resolve_404_maps():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "no match"})

    c = ZenoClient(base_url="http://api", transport=_transport(handler))
    try:
        c.resolve("asset://P/A/latest/fbx")
        assert False
    except ResolveNotFound:
        assert True


def test_blob_exists_404_false():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/exists")
        return httpx.Response(404, json={"detail": "nope"})

    c = ZenoClient(base_url="http://api", transport=_transport(handler))
    assert c.blob_exists("aa") is False


def test_get_blob_404_maps(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "missing"})

    c = ZenoClient(base_url="http://api", transport=_transport(handler))
    try:
        c.get_blob("aa", tmp_path / "out.bin")
        assert False
    except BlobNotFound:
        assert True


def test_upload_blob_400_mismatch_maps(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hi")

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == "/api/v1/cas/blobs"
        assert req.headers.get("X-Content-Hash") == "a" * 64
        return httpx.Response(400, json={"detail": "Content hash mismatch: expected a..., got b..."})

    c = ZenoClient(base_url="http://api", transport=_transport(handler))
    try:
        c.upload_blob(p, "a" * 64)
        assert False
    except ContentHashMismatch:
        assert True


def test_register_version_conflict_maps():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/versions"
        body = json.loads(req.content.decode("utf-8"))
        assert body["project"] == "P"
        return httpx.Response(409, json={"detail": "Version already exists"})

    c = ZenoClient(base_url="http://api", transport=_transport(handler))
    try:
        c.register_version(project="P", asset="A", representation="fbx", version="1", content_id="a" * 64)
        assert False
    except RegisterVersionConflict:
        assert True


def test_register_version_cas_missing_maps():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "Content not found in CAS"})

    c = ZenoClient(base_url="http://api", transport=_transport(handler))
    try:
        c.register_version(project="P", asset="A", representation="fbx", version="next", content_id="a" * 64)
        assert False
    except RegisterContentNotFound:
        assert True


def test_lock_acquire_409_maps():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/locks/acquire"
        return httpx.Response(409, json={"detail": "held"})

    c = ZenoClient(base_url="http://api", transport=_transport(handler))
    try:
        c.acquire_lock(user_id="u", session_id="s", project="p", asset="a", representation="fbx")
        assert False
    except LockHeldByOther:
        assert True

