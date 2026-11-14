from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tfroam.export import generate_zip, standalone_index, standalone_js
from tfroam.ui import dist

from .assets_util import build_assets
from .golden_util import FIXTURES, GOLDEN, load_js


def _read_js(zf: zipfile.ZipFile, name: str) -> object:
    text = zf.read(f"{name}.js").decode()
    assert text.startswith(f"const {name} = ")
    return json.loads(text.split("=", 1)[1])


@pytest.mark.parametrize("fixture", FIXTURES)
def test_zip_has_ui_and_data(fixture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assets = build_assets(fixture, tmp_path, monkeypatch)
    out = tmp_path / "out.zip"
    generate_zip(assets, out)
    with zipfile.ZipFile(out) as zf:
        assert zf.testzip() is None
        names = zf.namelist()
        # Go also wrote plan.js (never loaded by index.html); the port does not.
        assert names[-3:] == ["rso.js", "map.js", "graph.js"]
        assert "plan.js" not in names
        assert "index.html" in names and any(n.startswith("js/") for n in names)
        for name in ("rso", "map"):
            assert _read_js(zf, name) == load_js(GOLDEN / fixture / f"{name}.js")
        # graph list order is arbitrary in Go; compare by id
        got = _read_js(zf, "graph")
        want = load_js(GOLDEN / fixture / "graph.js")
        for key in ("nodes", "edges"):
            assert {i["data"]["id"]: i for i in got[key]} == {i["data"]["id"]: i for i in want[key]}  # type: ignore[index]


def test_standalone_index_matches_go_rewrite() -> None:
    html = '<html><head><link href="/css/a.css"></head><body><script src="/js/a.js"></script></body></html>'
    out = standalone_index(html)
    assert 'href="./css/a.css"' in out and 'src="./js/a.js"' in out
    assert out.index("./map.js") < out.index("./rso.js") < out.index("./graph.js")
    assert "plan.js" not in out  # Go does not load plan.js from index.html
    assert "</head>" not in out  # Go's Split drops the closing tag


def test_standalone_js_makes_public_path_relative() -> None:
    assert standalone_js(b'x=r.p+"img/a.png"') == b'x="./img/a.png"'


def test_frontend_files_are_all_included(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out.zip"
    generate_zip(build_assets("simple-test", tmp_path, monkeypatch), out)
    expected = {p.relative_to(Path(str(dist()))).as_posix() for p in Path(str(dist())).rglob("*") if p.is_file()}
    with zipfile.ZipFile(out) as zf:
        assert expected == set(zf.namelist()) - {"rso.js", "map.js", "graph.js"}
