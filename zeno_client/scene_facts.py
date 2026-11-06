"""DCC-independent helpers for reporting what a shot file contains."""
from __future__ import annotations

import re
from pathlib import PurePath
from typing import Any

_HASH = re.compile(r"^[0-9a-f]{64}$")


def content_id_from_path(path: str) -> str | None:
    """The content id of a file in the Zeno cache (``<cache>/<content_id>/<file>``), else None.

    Only whole-file versions live under their content id; chunked versions are rebuilt under
    the whole file's hash, so DCCs also tag what they link (see ``library_identity``).
    """
    parent = PurePath(str(path).replace("\\", "/")).parent.name.lower()
    return parent if _HASH.match(parent) else None


def library_identity(tag: dict[str, Any] | None, path: str) -> str | None:
    """Content id for a linked library: its Zeno tag first, then the cache path."""
    cid = str((tag or {}).get("content_id") or "").strip().lower()
    return cid if _HASH.match(cid) else content_id_from_path(path)


def facts_checks(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Submit-window rows for a scene facts report (levels: warning, info, ok)."""
    if not report:
        return []
    out: list[dict[str, Any]] = []
    rows = report.get("assets") or []
    for r in rows:
        if r["state"] == "outdated":
            out.append({
                "level": "warning",
                "text": f"{r['code']} v{r['version']:03d} is linked; v{r['approved_version']:03d} is approved.",
                "fix": {"action": "update_asset", "library": r.get("library"), "asset_code": r["code"],
                        "version": r["approved_version"], "representation": r.get("representation") or "blend",
                        "label": f"Update to v{r['approved_version']:03d}"},
            })
    for r in report.get("missing") or []:
        out.append({"level": "warning", "text": f"{r['code']} is cast in {report.get('shot', {}).get('code') or 'the shot'} but not linked in this file."})
    for r in rows:
        if r["state"] == "ahead":
            out.append({"level": "info", "text": f"{r['code']} v{r['version']:03d} is newer than approved v{r['approved_version']:03d}."})
        elif r["state"] == "unapproved":
            out.append({"level": "info", "text": f"{r['code']} v{r['version']:03d}: no version of {r['code']} is approved yet."})
    for r in report.get("not_cast") or []:
        out.append({"level": "info", "text": f"{r['code']} is in this file but not in {report.get('shot', {}).get('code') or 'the shot'}'s casting."})
    unknown = [r.get("library") or "?" for r in rows if r["state"] == "unknown"]
    if unknown:
        names = ", ".join(PurePath(str(u).replace("\\", "/")).name for u in unknown[:3]) + ("…" if len(unknown) > 3 else "")
        out.append({"level": "info", "text": f"{len(unknown)} linked file(s) are not Zeno versions: {names}."})
    current = [r for r in rows if r["state"] == "current"]
    if current and not any(o["level"] == "warning" for o in out):
        out.append({"level": "ok", "text": f"{len(current)} linked asset(s) are on their approved versions."})
    return out


def missing_libraries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Linked libraries to fetch on this machine.

    ``entries``: ``{"stored": path as saved in the file, "abspath", "exists", "tag"}`` per
    library. A library qualifies when its file is missing here and it can be identified as a
    Zeno version (tag or cache path). Libraries sharing one content id are fetched once.
    """
    out: list[dict[str, Any]] = []
    for e in entries:
        if e.get("exists"):
            continue
        cid = library_identity(e.get("tag"), str(e.get("abspath") or e.get("stored") or ""))
        if not cid:
            continue
        tag = e.get("tag") or {}
        out.append({
            "stored": e["stored"],
            "content_id": cid,
            "filename": PurePath(str(e.get("abspath") or e["stored"]).replace("\\", "/")).name or "linked.blend",
            "asset": tag.get("asset") or "",
            "version": tag.get("version"),
        })
    return out
