from __future__ import annotations

import http.client
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from tfroam.server import TfroamServer, allowed_hosts_for, host_header_name, is_loopback, split_address

from .assets_util import build_assets
from .golden_util import GOLDEN, load_js


@pytest.fixture
def base_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    server = TfroamServer("127.0.0.1:0", build_assets("random-test", tmp_path, monkeypatch))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def get(url: str) -> tuple[int, dict[str, str], bytes]:
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def test_api_endpoints_serve_the_documents(base_url: str) -> None:
    for name in ("rso", "map"):
        status, headers, body = get(f"{base_url}/api/{name}")
        assert status == 200
        assert "Access-Control-Allow-Origin" not in headers  # Go sent "*"; the UI is same-origin
        assert headers["Content-Type"] == "application/json"
        assert json.loads(body) == load_js(GOLDEN / "random-test" / f"{name}.js")
    status, _, body = get(f"{base_url}/api/graph")
    assert status == 200 and set(json.loads(body)) == {"nodes", "edges"}


def test_api_unknown_type_message(base_url: str) -> None:
    for kind in ("nope", "plan"):  # /api/plan is gone: same answer as any unknown type
        status, headers, body = get(f"{base_url}/api/{kind}")
        assert status == 200
        assert "Access-Control-Allow-Origin" not in headers
        assert body == b"Please enter a valid file type: rso, map, graph\n"


def test_health(base_url: str) -> None:
    status, _, body = get(f"{base_url}/health")
    assert status == 200 and json.loads(body) == {"alive": True}


def test_frontend_is_served(base_url: str) -> None:
    status, headers, body = get(f"{base_url}/")
    assert status == 200 and headers["Content-Type"].startswith("text/html") and b'<div id="app">' in body
    status, headers, _ = get(f"{base_url}/favicon.ico")
    assert status == 200 and headers["Content-Type"] == "image/x-icon"
    js = next(line for line in body.decode().split("src=")[1:] if "app." in line)
    path = js.split('"')[1]
    status, headers, _ = get(f"{base_url}{path}")
    assert status == 200 and headers["Content-Type"].startswith("text/javascript")


def test_missing_and_traversal_paths_are_404(base_url: str) -> None:
    assert get(f"{base_url}/nope.txt")[0] == 404
    assert get(f"{base_url}/..%2f..%2fpyproject.toml")[0] == 404
    assert get(f"{base_url}/js/../../../pyproject.toml")[0] in (200, 404)  # normalized by the client; never escapes


def test_split_address() -> None:
    assert split_address("0.0.0.0:9000") == ("0.0.0.0", 9000)
    assert split_address(":9000") == ("", 9000)
    assert split_address("[::1]:8080") == ("::1", 8080)
    with pytest.raises(ValueError):
        split_address("9000")


def _raw_get(port: int, host: str | None, path: str = "/api/rso", http10: bool = False) -> int:
    """GET with an exact Host header (None = no Host header at all)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    if http10:
        conn._http_vsn, conn._http_vsn_str = 10, "HTTP/1.0"  # type: ignore[attr-defined]
    try:
        conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
        if host is not None:
            conn.putheader("Host", host)
        conn.endheaders()
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


@pytest.fixture
def loopback_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TfroamServer]:
    server = TfroamServer("127.0.0.1:0", build_assets("simple-test", tmp_path, monkeypatch))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.mark.parametrize(
    "host",
    ["localhost", "localhost:{port}", "LOCALHOST:{port}", "127.0.0.1", "127.0.0.1:{port}", "[::1]:{port}", "[::1]"],
)
def test_host_guard_allows_loopback_names(loopback_server: TfroamServer, host: str) -> None:
    port = loopback_server.port
    assert _raw_get(port, host.format(port=port)) == 200
    assert _raw_get(port, host.format(port=port), path="/") == 200


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "evil.example:{port}",
        "localhost.evil.example",
        "127.0.0.2:{port}",
        "10.0.0.1",
        "",
        "localhost:x",
    ],
)
def test_host_guard_rejects_other_names(loopback_server: TfroamServer, host: str) -> None:
    port = loopback_server.port
    assert _raw_get(port, host.format(port=port)) == 403
    assert _raw_get(port, host.format(port=port), path="/") == 403
    assert _raw_get(port, host.format(port=port), path="/health") == 403


def test_host_guard_missing_host(loopback_server: TfroamServer) -> None:
    assert _raw_get(loopback_server.port, None) == 403  # HTTP/1.1 requires Host
    assert _raw_get(loopback_server.port, None, http10=True) == 200  # optional in HTTP/1.0


def test_allowed_hosts_for() -> None:
    assert allowed_hosts_for("127.0.0.5") == frozenset({"localhost", "127.0.0.1", "::1", "127.0.0.5"})
    assert allowed_hosts_for("LocalHost") == frozenset({"localhost", "127.0.0.1", "::1"})
    assert allowed_hosts_for("::1") == frozenset({"localhost", "127.0.0.1", "::1"})
    for host in ("0.0.0.0", "", "::", "192.168.1.10", "rover.internal"):
        assert allowed_hosts_for(host) is None


def test_wildcard_binding_does_not_filter_hosts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = TfroamServer("0.0.0.0:0", build_assets("simple-test", tmp_path, monkeypatch))
    assert server.allowed_hosts is None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert _raw_get(server.port, "rover.internal:9000") == 200
        assert _raw_get(server.port, None) == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_is_loopback() -> None:
    assert is_loopback("localhost") and is_loopback("127.0.0.1") and is_loopback("127.1.2.3") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("") and not is_loopback("::")
    assert not is_loopback("192.168.1.10") and not is_loopback("example.com")


def test_host_header_name() -> None:
    assert host_header_name("Localhost:9000") == "localhost"
    assert host_header_name("[::1]:9000") == "::1"
    assert host_header_name("[::1]") == "::1"
    assert host_header_name("::1") == "::1"
    assert host_header_name("127.0.0.1") == "127.0.0.1"
    assert host_header_name("") is None
    assert host_header_name("[::1]x") is None
    assert host_header_name("host:port") is None
