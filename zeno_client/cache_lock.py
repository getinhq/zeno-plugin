from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

from .cache_exceptions import CacheLockTimeoutError


@contextmanager
def file_lock(lock_path: str | Path, *, timeout_s: float = 60.0, poll_s: float = 0.1):
    """
    Cross-process lock using flock (POSIX). On Windows, falls back to an exclusive create lock.
    """
    p = Path(lock_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()
    f = None
    try:
        f = open(p, "a+b")
        if os.name == "posix":
            import fcntl

            while True:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if (time.time() - start) >= timeout_s:
                        raise CacheLockTimeoutError(f"Timed out acquiring lock: {p}")
                    time.sleep(poll_s)
        else:
            # Best-effort on non-POSIX: lock by exclusive file creation next to lock file
            sentinel = p.with_suffix(p.suffix + ".sentinel")
            while True:
                try:
                    fd = os.open(str(sentinel), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                    os.close(fd)
                    break
                except FileExistsError:
                    if (time.time() - start) >= timeout_s:
                        raise CacheLockTimeoutError(f"Timed out acquiring lock: {p}")
                    time.sleep(poll_s)
        yield
    finally:
        try:
            if f is not None and os.name == "posix":
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            if f is not None:
                f.close()
        except Exception:
            pass
        if os.name != "posix":
            try:
                sentinel = Path(lock_path).with_suffix(Path(lock_path).suffix + ".sentinel")
                if sentinel.exists():
                    sentinel.unlink()
            except Exception:
                pass

