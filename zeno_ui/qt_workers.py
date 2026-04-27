"""Qt-side async helpers for background IO without blocking the UI thread.

Uses ``QThreadPool`` + ``QRunnable`` and marshals results via a signal so
callers can update widgets safely on the Qt main thread.
"""
from __future__ import annotations

from typing import Any, Callable

from zeno_ui.qt_compat import get_qt_modules


def make_async_runner() -> Any:
    """Create an ``AsyncRunner`` QObject with ``finished(object)`` / ``failed(object)``.

    Factory function (instead of a top-level class) because ``QObject`` has to be
    subclassed lazily — importing QtCore at module import time would break
    stdlib-only environments that import ``zeno_ui`` for non-Qt workflows.
    """
    QtWidgets, QtCore, _ = get_qt_modules()

    class AsyncRunner(QtCore.QObject):
        finished = QtCore.Signal(object)  # type: ignore[arg-type]
        failed = QtCore.Signal(object)  # type: ignore[arg-type]

        def __init__(self, parent: Any | None = None) -> None:
            super().__init__(parent)
            self._pool = QtCore.QThreadPool.globalInstance()

        def submit(self, call: Callable[[], Any]) -> None:
            runner = _Runnable(call, self.finished, self.failed)
            self._pool.start(runner)

    class _Runnable(QtCore.QRunnable):
        def __init__(
            self,
            call: Callable[[], Any],
            done_sig: Any,
            fail_sig: Any,
        ) -> None:
            super().__init__()
            self._call = call
            self._done = done_sig
            self._fail = fail_sig
            self.setAutoDelete(True)

        def run(self) -> None:  # type: ignore[override]
            try:
                result = self._call()
            except BaseException as exc:  # noqa: BLE001
                try:
                    self._fail.emit(exc)
                except Exception:
                    pass
                return
            try:
                self._done.emit(result)
            except Exception:
                pass

    return AsyncRunner()


def make_debounced(callable_: Callable[[], None], interval_ms: int = 250) -> Callable[[], None]:
    """Return a callable that collapses rapid invocations into one call after ``interval_ms``."""
    _, QtCore, _ = get_qt_modules()

    timer = QtCore.QTimer()
    timer.setSingleShot(True)
    timer.setInterval(interval_ms)
    timer.timeout.connect(callable_)

    def _kick() -> None:
        timer.start()

    _kick._timer = timer  # type: ignore[attr-defined]  # keep alive
    return _kick


__all__ = ["make_async_runner", "make_debounced"]
