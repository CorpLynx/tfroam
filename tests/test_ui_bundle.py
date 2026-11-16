"""The prebuilt frontend in ``tfroam/ui/dist`` (built from ``ui/`` by ``scripts/build_ui.sh``).

These pin what the Python side and the project's privacy stance rely on, so a rebuild
that breaks ``-genImage``, ``-standalone`` or starts loading something from the network fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

from tfroam.export import standalone_index
from tfroam.ui import dist

DIST = Path(str(dist()))
URL = re.compile(rb"https?://|//[a-z0-9.-]+\.[a-z]{2,}/", re.IGNORECASE)


def _files(*suffixes: str) -> list[Path]:
    return sorted(p for p in DIST.rglob("*") if p.is_file() and p.suffix in suffixes)


def _app_js() -> bytes:
    (app,) = [p for p in _files(".js") if p.name.startswith("app.")]
    return app.read_bytes()


def test_bundle_has_the_expected_files() -> None:
    names = {p.relative_to(DIST).as_posix() for p in DIST.rglob("*") if p.is_file()}
    assert "index.html" in names and "favicon.ico" in names
    assert sum(n.startswith("js/app.") for n in names) == 1
    assert sum(n.startswith("js/chunk-vendors.") for n in names) == 1
    assert sum(n.startswith("css/app.") for n in names) == 1
    assert not [n for n in names if n.endswith(".map")]  # the source is in ui/; maps would triple the wheel


def test_index_references_only_its_own_assets() -> None:
    html = (DIST / "index.html").read_text(encoding="utf-8")
    assert '<div id="app">' in html
    refs = re.findall(r'(?:src|href)="?([^" >]+)', html)
    assert refs and all(ref.startswith("/") and not ref.startswith("//") for ref in refs)
    for ref in refs:
        assert (DIST / ref.lstrip("/")).is_file(), ref
    # Opened from disk (-standalone), every one of them becomes relative.
    assert '="/' not in standalone_index(html)


def test_scripts_load_no_assets_by_path() -> None:
    # -standalone rewrites only index.html. If a rebuild makes webpack emit images or other assets again,
    # the scripts would reference them through the public path ("/img/..."), which breaks under file://.
    names = {p.relative_to(DIST).as_posix() for p in DIST.rglob("*") if p.is_file()}
    assert not [n for n in names if n.startswith(("img/", "fonts/", "media/"))]
    for path in _files(".js"):
        assert b'.p+"' not in path.read_bytes(), path.name


def test_image_mode_hooks_are_present() -> None:
    # tfroam/screenshot.py waits for #cytoscape-div and clicks #saveGraph, which downloads tfroam.svg.
    scripts = b"".join(p.read_bytes() for p in _files(".js"))
    assert b"cytoscape-div" in scripts
    assert b"saveGraph" in _app_js() and b"tfroam.svg" in _app_js()


def test_page_reads_only_the_published_documents() -> None:
    app = _app_js()
    assert set(re.findall(rb"/api/(\w+)", app)) == {b"rso", b"map", b"graph"}


def test_nothing_is_loaded_from_another_host() -> None:
    # Our own code, the stylesheet and the page: no CDN, web font or analytics URL at all.
    # (The vendor chunk is third-party library code, which mentions URLs in strings and comments.)
    for path in [DIST / "index.html", *_files(".css")]:
        assert not URL.search(path.read_bytes()), path.name
    assert not URL.search(_app_js())
    for path in _files(".css"):
        css = path.read_bytes()
        assert b"@import" not in css and b"@font-face" not in css
