"""Read and parse ZENO_LAUNCH_CONTEXT from the environment (DCC plugins)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class LaunchContextV1:
    version: str
    intent: str
    project_id: str
    dcc: str
    dcc_label: Optional[str] = None
    dcc_executable_path: Optional[str] = None
    project_code: Optional[str] = None
    asset_id: Optional[str] = None
    shot_id: Optional[str] = None
    task_id: Optional[str] = None
    representation: Optional[str] = None
    version_spec: Optional[dict[str, Any]] = None
    resolved_path: Optional[str] = None
    api_base_url: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LaunchContextV1:
        return cls(
            version=str(d.get("version", "1")),
            intent=str(d["intent"]),
            project_id=str(d["project_id"]),
            dcc=str(d["dcc"]),
            dcc_label=d.get("dcc_label"),
            dcc_executable_path=d.get("dcc_executable_path"),
            project_code=d.get("project_code"),
            asset_id=d.get("asset_id"),
            shot_id=d.get("shot_id"),
            task_id=d.get("task_id"),
            representation=d.get("representation"),
            version_spec=d.get("version_spec"),
            resolved_path=d.get("resolved_path"),
            api_base_url=d.get("api_base_url"),
        )


def read_launch_context_from_environ() -> Optional[LaunchContextV1]:
    """Parse ZENO_LAUNCH_CONTEXT JSON if set."""
    raw = os.environ.get("ZENO_LAUNCH_CONTEXT")
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return LaunchContextV1.from_dict(data)
    except KeyError:
        return None


def apply_api_base_url_to_environ(ctx: LaunchContextV1) -> None:
    """If context carries api_base_url, set ZENO_API_BASE_URL for ZenoClient defaults."""
    if ctx.api_base_url:
        os.environ.setdefault("ZENO_API_BASE_URL", ctx.api_base_url)
