from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from pxr import Usd, UsdGeom
except Exception:  # pragma: no cover
    Usd = None
    UsdGeom = None


@dataclass(frozen=True)
class UsdSemanticSplit:
    static_payloads: list[bytes]
    volatile_payloads: list[bytes]


def is_usd_available() -> bool:
    return Usd is not None and UsdGeom is not None


def split_usd_semantic_payloads(path: str | Path) -> UsdSemanticSplit:
    """
    Optional USD semantic splitter:
    - static mesh topology payloads
    - volatile timesample payloads
    """
    if not is_usd_available():
        raise RuntimeError("OpenUSD (pxr) is not available")
    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise ValueError(f"Unable to open USD stage: {path}")
    static_payloads: list[bytes] = []
    volatile_payloads: list[bytes] = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        points_attr = mesh.GetPointsAttr()
        points = points_attr.Get()
        if points is not None:
            static_payloads.append(str(points).encode("utf-8"))
        ts = points_attr.GetTimeSamples()
        if ts:
            volatile_payloads.append(str(ts).encode("utf-8"))
    return UsdSemanticSplit(static_payloads=static_payloads, volatile_payloads=volatile_payloads)
