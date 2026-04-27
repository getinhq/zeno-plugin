"""Threaded stdlib HTTP server for the Chimera hub.

Stdlib-only on purpose — we already depend on ``httpx`` for client-side work,
but the hub's server surface stays tiny: a ``ThreadingHTTPServer`` with JSON
bodies, token auth, and pluggable handlers. Upgrading to FastAPI/uvicorn
later is a file-level replacement.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from chimera_hub import handlers as hub_handlers
from chimera_hub.ipc_contract import (
    AUTH_HEADER,
    CONTENT_TYPE_JSON,
    HEALTH_PATH,
    LOAD_PATH,
    PUBLISH_PATH,
    SHUTDOWN_PATH,
    UI_NAVIGATOR_PATH,
    UI_PALETTE_PATH,
    UI_RAISE_PATH,
    LoadRequest,
    PublishRequest,
    UiOpenRequest,
)

_log = logging.getLogger("chimera_hub.server")


@dataclass
class HubContext:
    """Dependencies handed to HTTP handlers.

    Defaults are lazy so importing this module does not import ``zeno_client``.
    """

    token: str
    client_factory: Callable[[], Any]
    cache_factory: Callable[[], Any]
    ui_service: Any | None = None
    on_shutdown: Callable[[], None] | None = None


def _default_client_factory() -> Any:
    from zeno_client import ZenoClient

    return ZenoClient()


def _default_cache_factory() -> Any:
    from zeno_client import CacheConfig, LocalCache

    return LocalCache(CacheConfig())


def build_default_context(*, token: str, ui_service: Any | None = None) -> HubContext:
    return HubContext(
        token=token,
        client_factory=_default_client_factory,
        cache_factory=_default_cache_factory,
        ui_service=ui_service,
    )


class _Handler(BaseHTTPRequestHandler):
    # ``ThreadingHTTPServer`` attaches ``hub_ctx`` on the server instance.
    server_version = "Chimera/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        _log.debug("%s - - %s", self.address_string(), format % args)

    # -- transport ------------------------------------------------------------
    def _read_json(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return None

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", CONTENT_TYPE_JSON)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _reject(self, status: int, message: str) -> None:
        self._send_json(status, {"ok": False, "message": message})

    def _authorised(self) -> bool:
        ctx: HubContext = self.server.hub_ctx  # type: ignore[attr-defined]
        presented = self.headers.get(AUTH_HEADER) or ""
        if presented and _consteq(presented, ctx.token):
            return True
        self._reject(HTTPStatus.UNAUTHORIZED, "invalid or missing token")
        return False

    # -- verbs ----------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != HEALTH_PATH:
            return self._reject(HTTPStatus.NOT_FOUND, f"no route {self.path}")
        if not self._authorised():
            return
        self._send_json(HTTPStatus.OK, hub_handlers.health())

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorised():
            return
        ctx: HubContext = self.server.hub_ctx  # type: ignore[attr-defined]
        body = self._read_json()
        if body is None:
            return self._reject(HTTPStatus.BAD_REQUEST, "invalid JSON body")

        route = self.path.split("?", 1)[0]
        if route == PUBLISH_PATH:
            req = _coerce_publish(body)
            return self._send_json(HTTPStatus.OK, hub_handlers.publish(req, client_factory=ctx.client_factory))
        if route == LOAD_PATH:
            req = _coerce_load(body)
            return self._send_json(
                HTTPStatus.OK,
                hub_handlers.load(req, client_factory=ctx.client_factory, cache_factory=ctx.cache_factory),
            )
        if route == UI_PALETTE_PATH:
            if ctx.ui_service is None:
                return self._reject(HTTPStatus.SERVICE_UNAVAILABLE, "UI not available in this hub process")
            req = _coerce_ui(body)
            return self._send_json(HTTPStatus.OK, hub_handlers.ui_open_palette(req, ui_service=ctx.ui_service))
        if route == UI_NAVIGATOR_PATH:
            if ctx.ui_service is None:
                return self._reject(HTTPStatus.SERVICE_UNAVAILABLE, "UI not available in this hub process")
            req = _coerce_ui(body)
            return self._send_json(HTTPStatus.OK, hub_handlers.ui_open_navigator(req, ui_service=ctx.ui_service))
        if route == UI_RAISE_PATH:
            if ctx.ui_service is None:
                return self._reject(HTTPStatus.SERVICE_UNAVAILABLE, "UI not available in this hub process")
            return self._send_json(HTTPStatus.OK, hub_handlers.ui_raise(ui_service=ctx.ui_service))
        if route == SHUTDOWN_PATH:
            if ctx.on_shutdown is not None:
                threading.Thread(target=ctx.on_shutdown, daemon=True).start()
            return self._send_json(HTTPStatus.OK, {"ok": True, "message": "shutting down"})
        self._reject(HTTPStatus.NOT_FOUND, f"no route {route}")


def _consteq(a: str, b: str) -> bool:
    """Constant-time string comparison (defeats trivial timing attacks)."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


def _coerce_publish(body: dict[str, Any]) -> PublishRequest:
    return PublishRequest(
        path=str(body.get("path") or ""),
        project=str(body.get("project") or ""),
        asset=str(body.get("asset") or ""),
        representation=str(body.get("representation") or "blend"),
        version=str(body.get("version") or "next"),
        dcc=str(body.get("dcc") or ""),
        extras=dict(body.get("extras") or {}),
    )


def _coerce_load(body: dict[str, Any]) -> LoadRequest:
    return LoadRequest(
        project=str(body.get("project") or ""),
        asset=str(body.get("asset") or ""),
        version=str(body.get("version") or "latest"),
        representation=str(body.get("representation") or "blend"),
    )


def _coerce_ui(body: dict[str, Any]) -> UiOpenRequest:
    hint = body.get("launch_hint")
    return UiOpenRequest(
        prefs_default_project=str(body.get("prefs_default_project") or ""),
        launch_hint=dict(hint) if isinstance(hint, dict) else None,
    )


def make_server(host: str, port: int, ctx: HubContext) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _Handler)
    server.hub_ctx = ctx  # type: ignore[attr-defined]
    return server


def serve_in_thread(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(
        target=server.serve_forever,
        name="chimera-hub-http",
        daemon=True,
    )
    thread.start()
    return thread


__all__ = ["HubContext", "build_default_context", "make_server", "serve_in_thread"]
