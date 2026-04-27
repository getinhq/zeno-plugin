from __future__ import annotations

from zeno_ui.stylesheet import load_stylesheet
from zeno_ui.workflows import (
    decode_project,
    format_project_enum,
    refresh_palette_state,
    sanitize_palette_fields,
)


def test_load_stylesheet_substitutes_all_tokens() -> None:
    qss = load_stylesheet()
    assert "{color." not in qss
    assert "{font." not in qss
    assert "#0a0a0a" in qss
    assert "#d4ff00" in qss


def test_decode_project() -> None:
    assert decode_project("abc|myproj") == ("abc", "myproj")
    assert decode_project("") == ("", "")


def test_format_project_enum() -> None:
    assert format_project_enum("id1", "CODE") == "id1|CODE"


def test_sanitize_palette_fields() -> None:
    assets = [{"code": "a", "name": "A"}, {"code": "b", "name": "B"}]
    ac, ver = sanitize_palette_fields(assets, "x", "latest")
    assert ac == "a"
    assert ver == "latest"


def test_refresh_palette_state_empty_project(monkeypatch) -> None:
    class Fake:
        def list_projects(self, *, code=None):
            return []

    st = refresh_palette_state(Fake(), project="", query="", asset_code="", version="latest")
    assert st.assets == []
    assert st.versions == []


def test_data_cache_caches_projects_and_assets() -> None:
    from zeno_ui.data_source import ZenoDataCache, refresh_palette_state_cached

    calls = {"projects": 0, "assets": 0, "groups": 0}

    class Fake:
        def list_projects(self, **_kwargs):
            calls["projects"] += 1
            return [{"id": "p1", "code": "PROJ", "name": "Proj"}]

        def list_assets(self, project_id):
            calls["assets"] += 1
            return [{"id": "a1", "code": "hero", "name": "Hero"}]

        def list_asset_version_groups(self, asset_id):
            calls["groups"] += 1
            return [{"version_number": 2}, {"version_number": 1}]

    cache = ZenoDataCache()
    client = Fake()

    st1 = refresh_palette_state_cached(
        cache, client, project_code="PROJ", query="", asset_code="", version="latest"
    )
    st2 = refresh_palette_state_cached(
        cache, client, project_code="PROJ", query="he", asset_code="", version="latest"
    )

    assert st1.asset_code == "hero"
    assert st2.versions == [2, 1]
    assert calls["projects"] == 1
    assert calls["assets"] == 1
    assert calls["groups"] == 1

    cache.clear()
    refresh_palette_state_cached(
        cache, client, project_code="PROJ", query="", asset_code="", version="latest"
    )
    assert calls["projects"] == 2
    assert calls["assets"] == 2
