"""``python -m chimera_hub`` — start the resident Chimera hub process.

Flags:
  --no-ui        run headless (no QApplication); UI endpoints will 503.
  --port PORT    bind to a fixed port instead of OS-picked ephemeral port.
  --foreground   keep the console attached; default is also foreground today.
"""
from __future__ import annotations

import argparse
import signal
import sys
import threading

from chimera_hub.runtime import (
    acquire_single_instance_or_die,
    clear_session,
    configure_logging,
    describe_root,
    prepare_session,
)
from chimera_hub.server import build_default_context, make_server, serve_in_thread


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="chimera_hub", description=__doc__)
    ap.add_argument("--no-ui", action="store_true", help="run headless (no Qt window).")
    ap.add_argument("--port", type=int, default=0, help="bind port (0 = OS-picked).")
    ap.add_argument("--host", default="127.0.0.1")
    return ap.parse_args(argv)


def _install_sigint(on_stop: callable) -> None:
    def _handler(signum, frame):  # noqa: ANN001
        on_stop()

    signal.signal(signal.SIGINT, _handler)
    try:
        signal.signal(signal.SIGTERM, _handler)
    except (AttributeError, ValueError):  # pragma: no cover - Windows
        pass


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    log = configure_logging()
    log.info("Starting Chimera hub %s", describe_root())

    acquire_single_instance_or_die()
    info = prepare_session(host=args.host, port=args.port or None)
    log.info("Bound %s:%d (pid=%d)", info.host, info.port, info.pid)

    ui_service = None
    app = None
    if not args.no_ui:
        try:
            from zeno_ui.qt_compat import ensure_qapplication

            app = ensure_qapplication()
            from chimera_hub.ui_service import HubUiService
            from zeno_client import ZenoClient

            from zeno_client.cache import LocalCache

            ui_service = HubUiService(
                client_factory=lambda: ZenoClient(),
                cache_factory=lambda: LocalCache(),
            )
            log.info("Qt UI available")
        except Exception as exc:
            log.warning("Qt unavailable, running headless: %s", exc)
            ui_service = None
            app = None

    stop_event = threading.Event()

    def _shutdown() -> None:
        stop_event.set()

    ctx = build_default_context(token=info.token, ui_service=ui_service)
    ctx.on_shutdown = _shutdown
    server = make_server(info.host, info.port, ctx)
    serve_thread = serve_in_thread(server)
    log.info("HTTP serving on http://%s:%d", info.host, info.port)

    _install_sigint(_shutdown)

    try:
        if app is not None:
            # Run Qt event loop on the main thread; stop when shutdown requested.
            from zeno_ui.qt_compat import get_qt_modules

            _, QtCore, _ = get_qt_modules()
            poll = QtCore.QTimer()
            poll.setInterval(100)

            def _check() -> None:
                if stop_event.is_set():
                    app.quit()

            poll.timeout.connect(_check)
            poll.start()
            app.exec() if hasattr(app, "exec") else app.exec_()
        else:
            stop_event.wait()
    finally:
        log.info("Shutting down hub")
        try:
            server.shutdown()
        except Exception:
            pass
        serve_thread.join(timeout=2.0)
        clear_session()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
