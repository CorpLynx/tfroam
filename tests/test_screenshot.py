from __future__ import annotations

import re
import threading
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tfroam import cli, screenshot
from tfroam.screenshot import ImageError, page_url, playwright_available

from .golden_util import GOLDEN


@pytest.fixture
def plan_json(tmp_path: Path) -> Path:
    import json

    from .golden_util import load_js

    path = tmp_path / "plan.json"
    path.write_text(json.dumps(load_js(GOLDEN / "simple-test" / "plan.js")))
    return path


def test_page_url_never_targets_a_wildcard_address() -> None:
    assert page_url("0.0.0.0:9000") == "http://127.0.0.1:9000"
    assert page_url(":9000") == "http://127.0.0.1:9000"
    assert page_url("127.0.0.1:8080") == "http://127.0.0.1:8080"
    assert page_url("[::1]:8080") == "http://[::1]:8080"


def test_missing_playwright_gives_install_hint(
    plan_json: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(screenshot, "playwright_available", lambda: False)
    monkeypatch.chdir(Path(__file__).parent / "fixtures" / "simple-test")
    code = cli.main(["-planJSONPath", str(plan_json), "-genImage", "true"])  # fails before serving anything
    assert code == 1
    assert "pip install 'tfroam[image]'" in capsys.readouterr().err


def _free_addr() -> str:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{s.getsockname()[1]}"


def test_image_mode_serves_then_shuts_down(
    plan_json: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    addr = _free_addr()
    seen: dict[str, Any] = {}

    fake_screenshot = _recording_screenshot(seen)
    monkeypatch.setattr(screenshot, "screenshot", fake_screenshot)
    monkeypatch.setattr(screenshot, "playwright_available", lambda: True)
    wd = Path(__file__).parent / "fixtures" / "simple-test"
    code = cli.main(["-workingDir", str(wd), "-planJSONPath", str(plan_json), "-ipPort", addr, "-genImage", "true"])
    assert code == 0 and seen == {"addr": addr, "health": b'{"alive": true}', "output": "rover.svg"}  # as given
    assert (tmp_path / "rover.svg").exists()
    assert "Server shut down." in capsys.readouterr().err


def _recording_screenshot(seen: dict[str, Any]) -> Any:
    def fake_screenshot(ip_port: str, output: str = "", **k: Any) -> None:
        seen["addr"] = ip_port
        seen["output"] = output
        with urllib.request.urlopen(f"http://{ip_port}/health") as resp:  # the server must be up already
            seen["health"] = resp.read()
        Path(output).write_text("<svg/>")

    return fake_screenshot


def test_image_mode_without_ip_port_uses_a_free_loopback_port(
    plan_json: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    monkeypatch.chdir(tmp_path)
    seen: dict[str, Any] = {}
    monkeypatch.setattr(screenshot, "screenshot", _recording_screenshot(seen))
    monkeypatch.setattr(screenshot, "playwright_available", lambda: True)
    wd = Path(__file__).parent / "fixtures" / "simple-test"
    # A Rover already serving on the default port must not matter. If 9000 can't be taken here,
    # something else holds it, which is the same situation.
    with socket.socket() as busy:
        try:
            busy.bind(("127.0.0.1", 9000))
            busy.listen()
        except OSError:
            pass
        assert cli.main(["-workingDir", str(wd), "-planJSONPath", str(plan_json), "-genImage"]) == 0
    host, _, port = seen["addr"].rpartition(":")
    assert host == "127.0.0.1" and int(port) not in (0, 9000)  # the port actually bound
    assert seen["health"] == b'{"alive": true}'


def test_image_path_flag_sets_the_output(plan_json: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "out").mkdir()
    seen: dict[str, Any] = {}
    monkeypatch.setattr(screenshot, "screenshot", _recording_screenshot(seen))
    monkeypatch.setattr(screenshot, "playwright_available", lambda: True)
    wd = Path(__file__).parent / "fixtures" / "simple-test"
    argv = ["-workingDir", str(wd), "-planJSONPath", str(plan_json), "-genImage", "-imagePath", "out/graph.svg"]
    assert cli.main(argv) == 0
    assert seen["output"] == "out/graph.svg" and (tmp_path / "out" / "graph.svg").exists()
    assert not (tmp_path / "rover.svg").exists()


def test_empty_image_path_is_rejected(plan_json: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screenshot, "playwright_available", lambda: True)
    assert cli.main(["-planJSONPath", str(plan_json), "-genImage", "-imagePath", ""]) == 1


def test_image_failure_exits_nonzero(
    plan_json: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    def boom(*a: Any, **k: Any) -> None:
        raise ImageError("browser exploded")

    monkeypatch.setattr(screenshot, "screenshot", boom)
    monkeypatch.setattr(screenshot, "playwright_available", lambda: True)
    wd = Path(__file__).parent / "fixtures" / "simple-test"
    code = cli.main(
        ["-workingDir", str(wd), "-planJSONPath", str(plan_json), "-ipPort", _free_addr(), "-genImage", "true"]
    )
    assert code == 1 and "browser exploded" in capsys.readouterr().err


def _browser_ready() -> bool:
    """True when a browser can really be launched (Playwright's own, ROVER_BROWSER_PATH, or a system one)."""
    if not playwright_available():
        return False
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            screenshot._launch(pw).close()
    except Exception:
        return False
    return True


def _signature(svg: str) -> tuple[Counter[str], list[str], tuple[str, str]]:
    """What is stable across runs. Go's own layout (positions, gradient ids) varies run to run."""
    size = re.search(r'width="([\d.]+)" height="([\d.]+)"', svg)
    assert size
    return (
        Counter(re.findall(r"<(\w+)", svg)),
        sorted(re.findall(r">([^<>\s][^<>]*)<", svg)),
        (size.group(1), size.group(2)),
    )


@pytest.mark.skipif(not _browser_ready(), reason="needs Playwright and a Chromium (set ROVER_BROWSER_PATH)")
def test_real_browser_svg_matches_go_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from tfroam.assets import generate_assets
    from tfroam.server import start_server

    from .golden_util import load_js

    monkeypatch.chdir(Path(__file__).parent / "fixtures" / "random-test")  # Go ran with -workingDir .
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(load_js(GOLDEN / "random-test" / "plan.js")))
    assets = generate_assets(working_dir=".", plan_json_path=str(plan))
    addr = _free_addr()
    server = start_server(addr, assets)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        out = tmp_path / "rover.svg"
        time.sleep(0.2)
        screenshot.screenshot(addr, out)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    got = _signature(out.read_text())
    want = _signature((GOLDEN / "random-test" / "rover.svg").read_text())
    assert got == want
