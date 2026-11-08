"""HTTP server for the interactive view (port of ``server.go``).

Serves the bundled frontend plus ``/api/{rso,map,graph}`` and ``/health``.
The Go version also served ``/api/plan`` and sent ``Access-Control-Allow-Origin: *``;
both are dropped here, and a loopback-bound server checks the ``Host`` header
against DNS rebinding.
"""

from __future__ import annotations

import ipaddress
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

log = logging.getLogger("tfroam")

_CONTENT_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
}
_INVALID_TYPE = "Please enter a valid file type: rso, map, graph\n"
_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def split_address(ip_port: str) -> tuple[str, int]:
    """``"0.0.0.0:9000"``, ``":9000"`` or ``"[::1]:9000"`` -> (host, port)."""
    host, sep, port = ip_port.rpartition(":")
    if not sep or not port.isdigit():
        raise ValueError(f"invalid address {ip_port!r}: expected host:port")
    return host.strip("[]"), int(port)


def is_loopback(host: str) -> bool:
    """``localhost`` or a literal loopback address (127.0.0.0/8, ``::1``)."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_header_name(value: str) -> str | None:
    """The hostname in a ``Host`` header (lower-cased, IPv6 without brackets); None if malformed."""
    value = value.strip().lower()
    if value.startswith("["):
        name, sep, rest = value[1:].partition("]")
        if not sep or (rest and not (rest.startswith(":") and rest[1:].isdigit())):
            return None
        return name
    if value.count(":") > 1:
        return value  # a bare IPv6 address
    name, sep, port = value.partition(":")
    if sep and not port.isdigit():
        return None
    return name or None


def allowed_hosts_for(bind_host: str) -> frozenset[str] | None:
    """``Host`` names accepted by a server bound to ``bind_host``; None = accept any.

    DNS-rebinding guard: only a loopback binding is filtered. A wildcard or LAN
    address (e.g. ``0.0.0.0`` in Docker) is reached under names we can't know.
    """
    if not is_loopback(bind_host):
        return None
    return _LOOPBACK_NAMES | {bind_host.lower()}


class RoverServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, ip_port: str, assets: Assets, frontend: Traversable | None = None) -> None:
        host, port = split_address(ip_port)
        self.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        self.documents = {name: dumps_json(doc).encode("utf-8") for name, doc in assets.documents().items()}
        self.frontend = frontend if frontend is not None else dist()
        self.allowed_hosts = allowed_hosts_for(host)
        super().__init__((host, port), _Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])


class _Handler(BaseHTTPRequestHandler):
    server: RoverServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature fixed by the base class
        pass  # the Go version does not log requests

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.do_GET()

    def _host_allowed(self) -> bool:
        allowed = self.server.allowed_hosts
        if allowed is None:
            return True
        hosts = self.headers.get_all("Host") or []
        if not hosts:
            return self.request_version == "HTTP/1.0"  # Host is optional only before HTTP/1.1
        return len(hosts) == 1 and host_header_name(hosts[0]) in allowed

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._send(403, b"403 Forbidden: unexpected Host header\n", "text/plain; charset=utf-8")
            return

        path = unquote(urlsplit(self.path).path)

        if path == "/health":
            self._send(200, b'{"alive": true}', "application/json")
        elif path.startswith("/api/"):
            kind = path.replace("/api/", "", 1)
            body = self.server.documents.get(kind)
            if body is None:
                self._send(200, _INVALID_TYPE.encode(), "text/plain; charset=utf-8")
            else:
                self._send(200, body, "application/json")
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
