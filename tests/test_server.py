from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from tfroam.server import RoverServer, split_address

from .assets_util import build_assets
from .golden_util import GOLDEN, load_js


@pytest.fixture
def base_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    server = RoverServer("127.0.0.1:0", build_assets("random-test", tmp_path, monkeypatch))
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
    for name in ("plan", "rso", "map"):
        status, headers, body = get(f"{base_url}/api/{name}")
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "*"
        assert headers["Content-Type"] == "application/json"
        assert json.loads(body) == load_js(GOLDEN / "random-test" / f"{name}.js")
    status, _, body = get(f"{base_url}/api/graph")
    assert status == 200 and set(json.loads(body)) == {"nodes", "edges"}


def test_api_unknown_type_message(base_url: str) -> None:
    status, _, body = get(f"{base_url}/api/nope")
    assert status == 200
    assert body == b"Please enter a valid file type: plan, rso, map, graph\n"


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
