"""
Maya ASCII (.ma) DCC canonicalizer
==================================
Normalizes save-time volatility before content-defined chunking (FastCDC / omni).

Maya ASCII is text. Typical volatile regions:

  - ``//Maya ASCII <version> scene`` header comment
  - ``fileInfo`` lines (application name, OS, build cut, license, dates, …)
  - ``requires maya "<year>"`` — changes when re-saved from another Maya version
  - ``//Last modified:`` and similar banner comments

The scene graph (nodes, attributes, connections) is left untouched so canonical
bytes remain valid Maya ASCII. Chunking runs on these canonical bytes when
``publish_chunked_file(..., dcc="maya")`` or when the path ends in ``.ma``.
"""
from __future__ import annotations

import os
import re
from typing import Final

# Autodesk / common volatile `fileInfo` keys (case-sensitive per Maya convention).
_VOLATILE_FILEINFO_KEYS: Final[frozenset[str]] = frozenset(
    {
        "application",
        "product",
        "version",
        "cutIdentifier",
        "os",
        "license",
        "apiVersion",
        "osv",
        "vcsInfo",
        "creationDate",
        "modifiedDate",
        "lastModified",
    }
)

_MAYA_ASCII_HEADER: Final[re.Pattern[str]] = re.compile(r"^(\s*)//.*\bMaya ASCII\b.*$")
_REQUIRES_MAYA: Final[re.Pattern[str]] = re.compile(
    r"^(\s*)requires\s+maya\s+\"[^\"]*\"\s*;\s*$",
    re.IGNORECASE,
)
_LAST_MODIFIED_COMMENT: Final[re.Pattern[str]] = re.compile(
    r"^(\s*)//.*\bLast modified:\s*.*$",
    re.IGNORECASE,
)
_CREATED_BY_MAYA: Final[re.Pattern[str]] = re.compile(
    r"^(\s*)//.*\bCreated by Maya\b.*$",
    re.IGNORECASE,
)

# UUID replacement: match `rename -uid "..."` or `-uid "..."` inside createNode
_UUID_RENAME: Final[re.Pattern[str]] = re.compile(
    r"^(\s*rename\s+-uid\s+)\"([^\"]+)\"(\s*;.*)$"
)

# Workspace variable filtering: match `workspace -fr "..." "..."`
_WORKSPACE_FR: Final[re.Pattern[str]] = re.compile(
    r"^(\s*workspace\s+-fr\s+\"[^\"]*\"\s+\"[^\"]*\"\s*;.*)$"
)

# ScriptNode detection
_SCRIPT_NODE_CREATE: Final[re.Pattern[str]] = re.compile(
    r"^\s*createNode\s+script\s+-n\s+\"(uiConfigurationScriptNode|sceneConfigurationScriptNode)\".*$"
)

# Script node string attribute injection (`setAttr ".b" -type "string" "...";`)
# Note: Maya strings can be multiline, so we need a state tracker.
_SET_ATTR_STRING_BEGIN: Final[re.Pattern[str]] = re.compile(
    r"^(\s*setAttr\s+\"(\.b|\.before|\.a|\.after)\"\s+-type\s+\"string\"\s+)\"(.*)$"
)

# Plugin/custom payloads known to be high-volatility and often huge.
_PLUGIN_VOLATILE_TYPE_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "ngst2SkinLayerDataStorage",
        "dataPolyComponent",
    }
)
_SET_ATTR_PLUGIN_PAYLOAD_BEGIN: Final[re.Pattern[str]] = re.compile(
    r'^(?P<prefix>\s*setAttr\s+.*?-type\s+"(?P<dtype>[^"]+)"\s+)"(?P<rest>.*)$'
)

# Numeric quantization targets.
_SET_ATTR_NUMERIC_TYPE: Final[re.Pattern[str]] = re.compile(
    r'^\s*setAttr\s+.*?-type\s+"(pointArray|vectorArray|double3|float3|polyFaces|nurbsCurve|nurbsSurface)"'
)
_SET_ATTR_KTV: Final[re.Pattern[str]] = re.compile(r'^\s*setAttr\s+.*"\.ktv\[')
_FLOAT_TOKEN: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_])(-?(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?)"
)

# fileInfo "key" "value"; — value may contain \" and \\
_FILEINFO_RE: Final[re.Pattern[str]] = re.compile(
    r"^(\s*)fileInfo\s+\"([^\"]+)\"\s+\"((?:[^\"\\]|\\.)*)\"\s*;\s*$"
)


def _normalize_fileinfo_line(line: str) -> str | None:
    """
    If line is a volatile fileInfo, return the canonical replacement.
    If line matches fileInfo but is not volatile, return None (keep line).
    If line does not match fileInfo, return None.
    """
    m = _FILEINFO_RE.match(line)
    if not m:
        return None
    key = m.group(2)
    if key not in _VOLATILE_FILEINFO_KEYS:
        return None
    indent = m.group(1)
    return f'{indent}fileInfo "{key}" "chimera.canonical";'


def _enabled(name: str, default: str = "0") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in ("1", "true", "yes", "on")


def _line_is_numeric_quantization_target(line: str) -> bool:
    return bool(_SET_ATTR_NUMERIC_TYPE.match(line) or _SET_ATTR_KTV.match(line))


def _quantize_float_tokens(line: str, decimals: int) -> str:
    """
    Quantize floating-point tokens outside quoted strings.
    """
    if decimals < 0:
        return line

    parts = line.split('"')
    for i in range(0, len(parts), 2):  # only non-quoted segments
        def _repl(m: re.Match[str]) -> str:
            token = m.group(1)
            try:
                value = float(token)
            except Exception:
                return token
            return f"{value:.{decimals}f}"

        parts[i] = _FLOAT_TOKEN.sub(_repl, parts[i])
    return '"'.join(parts)


def extract_semantic_anchors(canonical_data: bytes) -> list[int]:
    """
    Return deterministic byte offsets that are good chunk boundary candidates.
    """
    text = canonical_data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    anchors: list[int] = []
    cursor = 0

    for line in lines:
        stripped = line.lstrip()
        if (
            stripped.startswith("createNode animCurve")
            or ('-type "pointArray"' in line)
            or ('-type "polyFaces"' in line)
            or ('-type "nurbsCurve"' in line)
            or ('-type "nurbsSurface"' in line)
            or ('.ktv[' in line and stripped.startswith("setAttr "))
            or any(f'-type "{t}"' in line for t in _PLUGIN_VOLATILE_TYPE_ALLOWLIST)
        ):
            anchors.append(cursor)
        cursor += len(line.encode("utf-8")) + 1

    # Stable sorted unique offsets only.
    return sorted({a for a in anchors if a >= 0})


def canonicalize(raw_data: bytes) -> bytes:
    """
    Return canonical UTF-8 bytes for a Maya ASCII scene.

    - Strips UTF-8 BOM, normalizes newlines to ``\\n``
    - Normalizes ``// ... Maya ASCII ...`` header
    - Replaces volatile ``fileInfo`` values with a fixed placeholder
    - Normalizes ``requires maya "..."``
    - Drops ``// Last modified:`` lines; normalizes ``// Created by Maya ...``
    - Normalizes all ``rename -uid "..."`` to a zeroed UUID
    - Filters out specific volatile ``workspace -fr`` mapping lines
    - Neutralizes the contents of volatile UI scriptNodes (uiConfiguration / sceneConfiguration)

    Args:
        raw_data: Bytes read from disk (typically UTF-8).

    Returns:
        Canonical ``.ma`` text as UTF-8 bytes (no trailing newline added unless
        the input ended with a newline — we preserve split/join behavior).
    """
    text = raw_data.decode("utf-8-sig")
    # Normalize newlines only; do not strip meaningful spaces inside lines.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    out: list[str] = []

    # State machine for script node blanking
    in_volatile_script_node = False
    in_multiline_string = False
    string_indent = ""
    string_attr = ""
    # State machine for large plugin payload replacement
    in_multiline_plugin_payload = False
    plugin_prefix = ""

    do_quantize = _enabled("CHIMERA_MA_FLOAT_QUANTIZE", "0")
    quantize_places = int(str(os.environ.get("CHIMERA_MA_FLOAT_DECIMALS", "5")).strip() or "5")
    do_plugin_neutralize = _enabled("CHIMERA_MA_PLUGIN_PAYLOAD_NORMALIZE", "1")

    for line in lines:
        if in_multiline_plugin_payload:
            if re.search(r'(?<!\\)(?:\\\\)*";\s*$', line):
                in_multiline_plugin_payload = False
                out.append(f'{plugin_prefix}"chimera.canonical";')
            continue

        # 1) Handle multiline strings from volatile attributes
        if in_multiline_string:
            # If the string closes on this line without being escaped
            # (Checking for end quote not preceded by an odd number of backslashes)
            if re.search(r'(?<!\\)(?:\\\\)*";\s*$', line):
                in_multiline_string = False
                # Emit the sanitized placeholder once we reach the end of the giant string
                out.append(f'{string_indent}setAttr "{string_attr}" -type "string" "chimera.canonical";')
            continue

        # 2) Strip Last modified comments
        if _LAST_MODIFIED_COMMENT.match(line):
            continue

        # 3) Normalize UUIDs (crucial for dedup)
        uuid_m = _UUID_RENAME.match(line)
        if uuid_m:
            out.append(f'{uuid_m.group(1)}"00000000-0000-0000-0000-000000000000"{uuid_m.group(3)}')
            continue

        # 4) Filter workspace volatility
        if _WORKSPACE_FR.match(line):
            continue

        # 5) Normalize specific script nodes
        if _SCRIPT_NODE_CREATE.match(line):
            in_volatile_script_node = True
            out.append(line)
            continue
        
        # Reset volatile script node state if we hit a new node or block
        if in_volatile_script_node and (line.lstrip().startswith("createNode ") or line.lstrip().startswith("select ")):
            in_volatile_script_node = False

        if in_volatile_script_node:
            attr_m = _SET_ATTR_STRING_BEGIN.match(line)
            if attr_m:
                # We are setting a string attribute block on a volatile script node
                string_indent = attr_m.group(1).split("setAttr")[0]
                string_attr = attr_m.group(2)
                # Does the string close on the same line?
                remainder = attr_m.group(3)
                if re.search(r'(?<!\\)(?:\\\\)*";\s*$', remainder):
                    out.append(f'{string_indent}setAttr "{string_attr}" -type "string" "chimera.canonical";')
                else:
                    in_multiline_string = True
                continue

        if do_plugin_neutralize:
            plug_m = _SET_ATTR_PLUGIN_PAYLOAD_BEGIN.match(line)
            if plug_m:
                dtype = plug_m.group("dtype")
                if dtype in _PLUGIN_VOLATILE_TYPE_ALLOWLIST:
                    plugin_prefix = plug_m.group("prefix")
                    remainder = plug_m.group("rest")
                    if re.search(r'(?<!\\)(?:\\\\)*";\s*$', remainder):
                        out.append(f'{plugin_prefix}"chimera.canonical";')
                    else:
                        in_multiline_plugin_payload = True
                    continue

        # 6) Other standard normalization
        cm = _CREATED_BY_MAYA.match(line)
        if cm:
            out.append(f'{cm.group(1)}// Created by Maya')
            continue

        mm = _MAYA_ASCII_HEADER.match(line)
        if mm:
            out.append(f'{mm.group(1)}//Maya ASCII scene')
            continue

        rm = _REQUIRES_MAYA.match(line)
        if rm:
            out.append(f'{rm.group(1)}requires maya "chimera.canonical";')
            continue

        fi = _normalize_fileinfo_line(line)
        if fi is not None:
            out.append(fi)
            continue

        if do_quantize and _line_is_numeric_quantization_target(line) and '-type "string"' not in line:
            out.append(_quantize_float_tokens(line, quantize_places))
            continue

        out.append(line)

    return "\n".join(out).encode("utf-8")
