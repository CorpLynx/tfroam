"""HTTP server for the interactive view (port of ``server.go``).

Serves the bundled frontend plus ``/api/{plan,rso,map,graph}`` and ``/health``.
"""

from __future__ import annotations

import logging
import mimetypes
import posixpath
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources.abc import Traversable
from typing import Any
from urllib.parse import unquote, urlsplit

from .assets import Assets, dumps_json
from .ui import dist

log = logging.getLogger("rover")

_CONTENT_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
}
_INVALID_TYPE = "Please enter a valid file type: plan, rso, map, graph\n"


def split_address(ip_port: str) -> tuple[str, int]:
    """``"0.0.0.0:9000"``, ``":9000"`` or ``"[::1]:9000"`` -> (host, port)."""
    host, sep, port = ip_port.rpartition(":")
    if not sep or not port.isdigit():
        raise ValueError(f"invalid address {ip_port!r}: expected host:port")
    return host.strip("[]"), int(port)


class RoverServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, ip_port: str, assets: Assets, frontend: Traversable | None = None) -> None:
        host, port = split_address(ip_port)
        self.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        self.documents = {name: dumps_json(doc).encode("utf-8") for name, doc in assets.documents().items()}
        self.frontend = frontend if frontend is not None else dist()
        super().__init__((host, port), _Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])


class _Handler(BaseHTTPRequestHandler):
    server: RoverServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature fixed by the base class
        pass  # the Go version does not log requests

    def _send(self, status: int, body: bytes, content_type: str, cors: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)

        if path == "/health":
            self._send(200, b'{"alive": true}', "application/json")
        elif path.startswith("/api/"):
            kind = path.replace("/api/", "", 1)
            body = self.server.documents.get(kind)
            if body is None:
                self._send(200, _INVALID_TYPE.encode(), "text/plain; charset=utf-8", cors=True)
            else:
                self._send(200, body, "application/json", cors=True)
        else:
            self._static(path)

    def _static(self, path: str) -> None:
        parts = [p for p in posixpath.normpath("/" + path).split("/") if p]
        if path.endswith("/") or not parts:
            parts.append("index.html")
        node = self.server.frontend
        for part in parts:
            node = node.joinpath(part)
        if ".." in path.split("/") or not node.is_file():
            self._send(404, b"404 page not found\n", "text/plain; charset=utf-8")
            return
        suffix = posixpath.splitext(parts[-1])[1].lower()
        ctype = _CONTENT_TYPES.get(suffix) or mimetypes.guess_type(parts[-1])[0] or "application/octet-stream"
        self._send(200, node.read_bytes(), ctype)


def start_server(ip_port: str, assets: Assets, frontend: Traversable | None = None) -> RoverServer:
    """Bind the listening socket (the browser can connect as soon as this returns)."""
    log.info("Rover is running on %s", ip_port)
    return RoverServer(ip_port, assets, frontend)
