from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zeno_client.palette_catalog import filter_assets


def decode_project(value: str) -> tuple[str, str]:
    """Split Blender enum storage ``project_id|project_code``."""
    if "|" not in value:
        return "", ""
    pid, code = value.split("|", 1)
    return pid, code


def format_project_enum(project_id: str, project_code: str) -> str:
    return f"{project_id}|{project_code}"


def list_projects_for_navigator(client: Any) -> list[dict[str, Any]]:
    """Rows for project picker: id, code, name, enum_value."""
    try:
        projects = client.list_projects(status="active")
    except Exception:
        projects = []
    out: list[dict[str, Any]] = []
    for p in projects:
        pid = str(p.get("id") or "")
        code = str(p.get("code") or "")
        name = str(p.get("name") or code or pid)
        out.append(
            {
                "id": pid,
                "code": code,
                "name": name,
                "enum_value": format_project_enum(pid, code),
            }
        )
    return out


def list_assets_for_project(client: Any, project_id: str) -> list[dict[str, Any]]:
    """Rows for asset picker: code, name, enum_value (code)."""
    if not project_id:
        return []
    try:
        assets = client.list_assets(project_id)
    except Exception:
        assets = []
    out: list[dict[str, Any]] = []
    for a in assets:
        code = str(a.get("code") or "")
        name = str(a.get("name") or code)
        out.append({"code": code, "name": name, "enum_value": code, "raw": a})
    return out


def navigator_launch_hint_enum(
    hint: dict[str, Any] | None,
    projects: list[dict[str, Any]],
) -> str | None:
    """If launch context matches a listed project, return its enum_value."""
    if not hint:
        return None
    pid = str(hint.get("project_id") or "")
    pcode = str(hint.get("project_code") or "")
    if not pid or not pcode:
        return None
    target = format_project_enum(pid, pcode)
    valid = {p["enum_value"] for p in projects}
    return target if target in valid else None


@dataclass
class PaletteRefreshResult:
    assets: list[dict[str, Any]]
    versions: list[int]
    asset_code: str
    version: str


def sanitize_palette_fields(
    assets: list[dict[str, Any]],
    asset_code: str,
    version: str,
) -> tuple[str, str]:
    """Keep asset/version props consistent with the current asset list (UI-only)."""
    valid_codes = [str(a.get("code") or "").strip() for a in assets if str(a.get("code") or "").strip()]
    ac = asset_code.strip()
    if valid_codes and ac not in valid_codes:
        ac = valid_codes[0]
    ver = (version or "latest").strip() or "latest"
    return ac, ver


def _sanitize_palette_selection(
    assets: list[dict[str, Any]],
    asset_code: str,
    version: str,
) -> tuple[str, str]:
    return sanitize_palette_fields(assets, asset_code, version)


def refresh_palette_state(
    client: Any,
    *,
    project: str,
    query: str,
    asset_code: str,
    version: str,
) -> PaletteRefreshResult:
    """Mirror Blender ``CHIMERA_OT_palette_open._refresh`` + sanitize."""
    project = (project or "").strip()
    query = query.strip()
    try:
        projects = client.list_projects(code=project) if project else []
        if not projects:
            return PaletteRefreshResult([], [], asset_code, version)
        pid = str(projects[0].get("id") or "")
        assets = client.list_assets(pid)
        filtered = filter_assets(assets, query)
        ac, ver = _sanitize_palette_selection(filtered, asset_code, version)
        if not filtered:
            return PaletteRefreshResult([], [], ac, ver)
        asset: dict[str, Any] | None = None
        for a in filtered:
            if str(a.get("code") or "").strip() == ac:
                asset = a
                break
        if not asset:
            return PaletteRefreshResult(filtered, [], ac, ver)
        groups = client.list_asset_version_groups(str(asset.get("id") or ""))
        nums: list[int] = []
        for g in groups:
            try:
                nums.append(int(g.get("version_number")))
            except Exception:
                pass
        nums.sort(reverse=True)
        ac, ver = _sanitize_palette_selection(filtered, ac, ver)
        return PaletteRefreshResult(filtered, nums, ac, ver)
    except Exception:
        return PaletteRefreshResult([], [], asset_code, version)


def resolve_palette_default_project(
    client: Any,
    *,
    prefs_default: str,
    hint: dict[str, Any] | None,
) -> str:
    """Default project code from addon prefs, then launch hint."""
    project = (prefs_default or "").strip()
    if project:
        return project
    if not hint:
        return ""
    if hint.get("project_code"):
        return str(hint["project_code"]).strip()
    if hint.get("project_id"):
        try:
            for p in client.list_projects():
                if str(p.get("id")) == str(hint["project_id"]):
                    return str(p.get("code") or "").strip()
        except Exception:
            pass
    return ""


__all__ = [
    "PaletteRefreshResult",
    "decode_project",
    "format_project_enum",
    "list_assets_for_project",
    "list_projects_for_navigator",
    "navigator_launch_hint_enum",
    "refresh_palette_state",
    "resolve_palette_default_project",
    "sanitize_palette_fields",
]
