"""Silent provisioning of PySide6 into Blender's bundled Python.

The addon expects to be able to ``import PySide6`` from within Blender's
own Python interpreter. There are two problems to solve:

1. **Install.** Blender ships without PySide6. We detect Blender's *real*
   Python binary (``sys.executable`` is the Blender binary from 2.92+,
   not Python) and run ``python -m pip install --user PySide6`` via it.
2. **Import path.** Even after install, the running Python process may
   not have ``~/.local/lib/python3.X/site-packages`` on ``sys.path``
   (Blender sometimes starts with ``ENABLE_USER_SITE=False`` or simply
   skips user-site). ``ensure_pyside_on_path()`` appends every plausible
   candidate and invalidates import caches before the next ``import``.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import site
import subprocess
import sys
import sysconfig
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_INSTALL_THREAD: threading.Thread | None = None
_RESULT: dict[str, Any] = {"done": False, "ok": False, "message": ""}
_PATH_PATCHED = False


# ---------------------------------------------------------------------------
# Blender Python binary discovery (``sys.executable`` is Blender itself on 2.92+).
# ---------------------------------------------------------------------------

def _discover_python_binary() -> str:
    """Return the actual Python interpreter that Blender is running.

    Tries ``sys._base_executable`` (set by the embedded launcher), then looks
    for ``python``/``python3``/``python3.X`` under ``sys.prefix/bin`` (POSIX)
    or ``sys.prefix`` (Windows). Falls back to ``sys.executable`` so pip
    invocation at least prints a clear error if none match.
    """
    base = getattr(sys, "_base_executable", "") or ""
    if base and Path(base).is_file() and "python" in Path(base).name.lower():
        return base

    prefix = Path(sys.prefix)
    candidates: list[Path] = []
    if os.name == "nt":  # pragma: no cover - platform specific
        candidates.extend([prefix / "python.exe", prefix / "bin" / "python.exe"])
    else:
        py_x_y = f"python{sys.version_info.major}.{sys.version_info.minor}"
        candidates.extend(
            [
                prefix / "bin" / py_x_y,
                prefix / "bin" / f"python{sys.version_info.major}",
                prefix / "bin" / "python",
            ]
        )
    for c in candidates:
        if c.is_file():
            return str(c)
    return sys.executable


# ---------------------------------------------------------------------------
# sys.path patching — ensure pip's install dirs are visible to the import system.
# ---------------------------------------------------------------------------

def _candidate_site_dirs() -> list[Path]:
    """Return every directory where ``pip install --user`` might have put PySide6."""
    dirs: list[Path] = []

    try:
        user_site = site.getusersitepackages()
    except Exception:
        user_site = ""
    if user_site:
        dirs.append(Path(user_site))

    for env_var in ("PYTHONUSERBASE", "CHIMERA_EXTRA_SITE"):
        base = os.environ.get(env_var, "").strip()
        if not base:
            continue
        base_p = Path(base).expanduser()
        dirs.append(base_p / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
        dirs.append(base_p)

    try:
        purelib = sysconfig.get_path("purelib", vars={"userbase": os.path.expanduser("~/.local")})
        if purelib:
            dirs.append(Path(purelib))
    except Exception:
        pass

    default_user_bases = [
        Path.home() / ".local" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
    ]
    if os.name == "nt":  # pragma: no cover
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            default_user_bases.append(
                Path(appdata)
                / "Python"
                / f"Python{sys.version_info.major}{sys.version_info.minor}"
                / "site-packages"
            )
    dirs.extend(default_user_bases)

    seen: set[str] = set()
    unique: list[Path] = []
    for d in dirs:
        s = str(d)
        if s and s not in seen:
            seen.add(s)
            unique.append(d)
    return unique


def ensure_pyside_on_path() -> list[str]:
    """Add every plausible user/site install dir to ``sys.path``.

    Returns the list of directories that were actually added (existed on disk
    and weren't already on ``sys.path``). Safe to call many times.
    """
    global _PATH_PATCHED
    added: list[str] = []
    for candidate in _candidate_site_dirs():
        if not candidate.is_dir():
            continue
        s = str(candidate)
        if s in sys.path:
            continue
        sys.path.insert(0, s)
        added.append(s)
    if added:
        try:
            importlib.invalidate_caches()
        except Exception:
            pass
    _PATH_PATCHED = True
    return added


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------

def pyside_available() -> bool:
    """``True`` if ``PySide6`` (or PySide2) can be imported *right now*.

    Side effect: if the first attempt fails but the files exist on disk under
    a user-site directory, we append that directory to ``sys.path`` and retry
    — the common Blender-on-macOS failure mode where ``--user`` installs land
    somewhere Blender's Python isn't looking.
    """
    for mod in ("PySide6", "PySide2"):
        if importlib.util.find_spec(mod) is not None:
            return True

    ensure_pyside_on_path()

    for mod in ("PySide6", "PySide2"):
        if importlib.util.find_spec(mod) is not None:
            return True
    return False


def _do_install(python_binary: str) -> None:
    global _RESULT
    _RESULT = {"done": False, "ok": False, "message": f"installing with {python_binary}"}
    cmd = [python_binary, "-m", "pip", "install", "--upgrade", "--user", "PySide6"]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=600,
            check=False,
        )
        ok = proc.returncode == 0
        msg = (proc.stdout or b"").decode("utf-8", errors="replace")[-2000:]
    except Exception as exc:  # noqa: BLE001
        ok = False
        msg = f"{type(exc).__name__}: {exc}"
    if ok:
        ensure_pyside_on_path()
    _RESULT = {"done": True, "ok": ok, "message": msg, "python": python_binary}


def ensure_pyside_async() -> dict[str, Any]:
    """Kick off a background install if PySide is missing.

    Returns the current install state. Idempotent — caller can poll
    ``pyside_available()`` after the thread completes.
    """
    global _INSTALL_THREAD

    if pyside_available():
        return {"done": True, "ok": True, "message": "PySide already available"}

    if os.environ.get("CHIMERA_DISABLE_PYSIDE_INSTALL", "").strip() in ("1", "true", "yes"):
        return {"done": True, "ok": False, "message": "Auto-install disabled by env"}

    py = _discover_python_binary()

    with _LOCK:
        if _INSTALL_THREAD is not None and _INSTALL_THREAD.is_alive():
            return {"done": False, "ok": False, "message": "PySide install in progress", "python": py}

        _INSTALL_THREAD = threading.Thread(
            target=_do_install,
            args=(py,),
            name="chimera-pyside-install",
            daemon=True,
        )
        _INSTALL_THREAD.start()

    return {"done": False, "ok": False, "message": "PySide install started", "python": py}


def install_state() -> dict[str, Any]:
    return dict(_RESULT)


def diagnostics() -> dict[str, Any]:
    """Report where PySide is / is not, to help the user debug."""
    ensure_pyside_on_path()
    spec6 = importlib.util.find_spec("PySide6")
    spec2 = importlib.util.find_spec("PySide2")
    return {
        "python_executable": sys.executable,
        "python_binary_guess": _discover_python_binary(),
        "python_prefix": sys.prefix,
        "python_version": sys.version,
        "PySide6": getattr(spec6, "origin", None) if spec6 else None,
        "PySide2": getattr(spec2, "origin", None) if spec2 else None,
        "search_dirs": [str(p) for p in _candidate_site_dirs()],
        "sys_path_snapshot": list(sys.path),
        "install_state": install_state(),
    }


__all__ = [
    "diagnostics",
    "ensure_pyside_async",
    "ensure_pyside_on_path",
    "install_state",
    "pyside_available",
]
