from __future__ import annotations

from typing import Any


def build_asset_uri(project_code: str, asset_code: str, version_spec: str | int, representation: str) -> str:
    return f"asset://{project_code}/{asset_code}/{version_spec}/{representation}"


def filter_assets(assets: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    q = query.strip().lower()
    if not q:
        return list(assets)
    out: list[dict[str, Any]] = []
    for a in assets:
        code = str(a.get("code") or "").lower()
        name = str(a.get("name") or "").lower()
        typ = str(a.get("type") or "").lower()
        if q in code or q in name or q in typ:
            out.append(a)
    return out

