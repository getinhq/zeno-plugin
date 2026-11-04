import json

import httpx

from zeno_client.client import ZenoClient
from zeno_client.scene_facts import content_id_from_path, facts_checks, library_identity

CID = "ab" * 32


def test_content_id_from_cache_paths():
    assert content_id_from_path(f"/Users/a/.chimera/cache/{CID}/nono.blend") == CID
    assert content_id_from_path(f"C:\\cache\\{CID.upper()}\\nono.blend") == CID
    assert content_id_from_path("/nas/assets/nono/nono_v003.blend") is None
    assert content_id_from_path("//libs/nono.blend") is None


def test_library_identity_prefers_tag():
    other = "cd" * 32
    assert library_identity({"content_id": other}, f"/c/{CID}/x.blend") == other
    assert library_identity({}, f"/c/{CID}/x.blend") == CID
    assert library_identity({"content_id": "not-a-hash"}, "/nas/x.blend") is None


def test_facts_checks_rows():
    report = {
        "shot": {"code": "SH0010"},
        "assets": [
            {"code": "nono", "version": 1, "approved_version": 2, "state": "outdated", "library": "//nono.blend", "representation": "blend"},
            {"code": "mug", "version": 1, "approved_version": None, "state": "unapproved", "library": "//mug.blend"},
            {"code": "tree", "version": 4, "approved_version": 3, "state": "ahead", "library": "//tree.blend"},
            {"library": "/nas/old/rock.blend", "state": "unknown"},
        ],
        "not_cast": [{"code": "mug"}],
        "missing": [{"code": "grandma"}],
    }
    rows = facts_checks(report)
    texts = [r["text"] for r in rows]
    assert rows[0]["level"] == "warning" and texts[0] == "nono v001 is linked; v002 is approved."
    assert rows[0]["fix"] == {"action": "update_asset", "library": "//nono.blend", "asset_code": "nono", "version": 2,
                              "representation": "blend", "label": "Update to v002"}
    assert "grandma is cast in SH0010 but not linked in this file." in texts
    assert "tree v004 is newer than approved v003." in texts
    assert "mug v001: no version of mug is approved yet." in texts
    assert "mug is in this file but not in SH0010's casting." in texts
    assert "1 linked file(s) are not Zeno versions: rock.blend." in texts
    assert not any(r["level"] == "ok" for r in rows)  # no all-clear while something is off
    ok = facts_checks({"assets": [{"code": "nono", "version": 2, "approved_version": 2, "state": "current"}]})
    assert [(r["level"], r["text"]) for r in ok] == [("ok", "1 linked asset(s) are on their approved versions.")]
    assert facts_checks(None) == []


def test_client_scene_facts_calls():
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path, json.loads(req.content)))
        if req.url.path.endswith("/lookup"):
            return httpx.Response(200, json=[{"content_id": CID, "version": 3}])
        return httpx.Response(200, json={"facts": {}, "report": {"assets": []}})

    c = ZenoClient(base_url="http://api", transport=httpx.MockTransport(handler))
    assert c.put_scene_facts("s1", {"frame_start": 1})["report"] == {"assets": []}
    assert c.lookup_versions([CID])[0]["version"] == 3
    assert calls == [("PUT", "/api/v1/shots/s1/scene-facts", {"frame_start": 1}),
                     ("POST", "/api/v1/versions/lookup", {"content_ids": [CID]})]


from zeno_client.scene_facts import missing_libraries  # noqa: E402


def test_missing_libraries():
    other = "cd" * 32
    entries = [
        {"stored": f"/Users/sam/.chimera/cache/{CID}/nono.blend", "abspath": f"/Users/sam/.chimera/cache/{CID}/nono.blend",
         "exists": False, "tag": {"content_id": CID, "asset": "nono", "version": 2}},
        {"stored": f"/Users/sam/.chimera/cache/{other}/mug.blend", "abspath": f"/Users/sam/.chimera/cache/{other}/mug.blend",
         "exists": False, "tag": None},  # untagged, but the cache path names it
        {"stored": "/Users/me/.chimera/cache/x/tree.blend", "abspath": "/Users/me/.chimera/cache/x/tree.blend", "exists": True, "tag": {}},
        {"stored": "/nas/old/rock.blend", "abspath": "/nas/old/rock.blend", "exists": False, "tag": None},  # not Zeno: leave it
    ]
    got = missing_libraries(entries)
    assert [(g["content_id"], g["filename"], g["asset"], g["version"]) for g in got] == [
        (CID, "nono.blend", "nono", 2), (other, "mug.blend", "", None)]
