"""
OpenUSD canonicalization layer.

Supported inputs:
  - .usda text files
  - .usdc crate binaries
  - .usd (auto-detected by signature)
  - .usdz archives (optional deterministic repack mode)
"""
from __future__ import annotations

import io
import os
import zipfile
from typing import Literal

UsdFormat = Literal["usda", "usdc", "usdz", "unknown"]

_USDA_MAGIC = b"#usda"
_USDC_MAGIC = b"PXR-USDC"
_ZIP_MAGIC = b"PK\x03\x04"
_FIXED_ZIP_DT = (1980, 1, 1, 0, 0, 0)


def detect_usd_format(raw_data: bytes) -> UsdFormat:
    """
    Detect which OpenUSD container this byte stream represents.
    """
    head = raw_data[:64]
    stripped = raw_data.lstrip()[:64]
    if stripped.startswith(_USDA_MAGIC):
        return "usda"
    if head.startswith(_USDC_MAGIC):
        return "usdc"
    if head.startswith(_ZIP_MAGIC):
        return "usdz"
    return "unknown"


def _is_usd_like_name(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(".usd") or lower.endswith(".usda") or lower.endswith(".usdc")


def _repack_usdz_deterministic(raw_data: bytes) -> bytes:
    """
    Rebuild a USDZ archive with deterministic metadata and ordering.
    """
    in_buf = io.BytesIO(raw_data)
    out_buf = io.BytesIO()

    try:
        with zipfile.ZipFile(in_buf, "r") as zin:
            infos = [i for i in zin.infolist() if i.filename]
            # Stable order avoids save-time archive ordering drift.
            infos.sort(key=lambda i: i.filename)

            with zipfile.ZipFile(out_buf, "w", compression=zipfile.ZIP_STORED, strict_timestamps=False) as zout:
                for info in infos:
                    name = info.filename
                    if name.endswith("/"):
                        zinfo = zipfile.ZipInfo(filename=name, date_time=_FIXED_ZIP_DT)
                        zinfo.compress_type = zipfile.ZIP_STORED
                        zinfo.external_attr = 0
                        zinfo.create_system = 3
                        zout.writestr(zinfo, b"")
                        continue

                    payload = zin.read(name)
                    if _is_usd_like_name(name):
                        payload = canonicalize(payload)

                    zinfo = zipfile.ZipInfo(filename=name, date_time=_FIXED_ZIP_DT)
                    zinfo.compress_type = zipfile.ZIP_STORED
                    zinfo.external_attr = 0
                    zinfo.create_system = 3
                    zout.writestr(zinfo, payload)
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid USDZ archive: cannot parse ZIP structure") from exc

    return out_buf.getvalue()


def canonicalize(raw_data: bytes) -> bytes:
    """
    Return canonical bytes for USD-family inputs.

    Behavior:
      - USDA/USDC/unknown .usd: pass-through bytes
      - USDZ: deterministic extract+repack when CHIMERA_USDZ_REPACK=1; else pass-through
    """
    fmt = detect_usd_format(raw_data)
    if fmt in ("usda", "usdc", "unknown"):
        return raw_data

    # USDZ handling
    repack = str(os.environ.get("CHIMERA_USDZ_REPACK", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not repack:
        return raw_data
    return _repack_usdz_deterministic(raw_data)
