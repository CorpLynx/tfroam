"""Standalone mode: the UI and the data as files that open from disk.

``generate_zip`` writes ``<name>.zip`` (``-standalone``); ``write_site`` writes the same files to a
directory (used by ``tfroam plan`` in "page" mode, see ``auto.py``).
"""

from __future__ import annotations

import os
import shutil
import zipfile
from collections.abc import Iterator
from importlib.resources.abc import Traversable
from pathlib import Path

from .assets import Assets, dumps_js
from .ui import dist

# Go's zip writer stores no timestamp; use the zip epoch so output is reproducible.
_EPOCH = (1980, 1, 1, 0, 0, 0)

# index.html loads these as scripts (fetching JSON from file:// would hit CORS).
_DATA_SCRIPTS = ("map.js", "rso.js", "graph.js")


def standalone_index(html: str) -> str:
    """Rewrite the served ``index.html`` for opening from disk."""
    head, _, tail = html.partition("</head>")  # like Go's Split, the "</head>" tag itself is dropped
    # Same whitespace as the Go version, so index.html is byte-identical.
    scripts = "\n\t\t".join(
        f'<script type="text/javascript" language="javascript" src="./{n}"></script>' for n in _DATA_SCRIPTS
    )
    return (head + scripts + tail).replace('="/', '="./')


def _entries(root: Traversable) -> list[tuple[str, Traversable]]:
    """Files at the top level, then one directory level down, in name order (as the Go version does)."""
    out: list[tuple[str, Traversable]] = []
    for item in sorted(root.iterdir(), key=lambda i: i.name):
        if not item.is_dir():
            out.append((item.name, item))
            continue
        for sub in sorted(item.iterdir(), key=lambda i: i.name):
            out.append((f"{item.name}/{sub.name}", sub))
    return out


def site_files(assets: Assets, frontend: Traversable | None = None) -> Iterator[tuple[str, bytes]]:
    """Every file of the standalone page, as (relative path, content), in zip order."""
    root = frontend if frontend is not None else dist()
    for name, item in _entries(root):
        data = item.read_bytes()
        # Only index.html needs rewriting: the bundle loads no images or other assets by path
        # (tests/test_ui_bundle.py pins that), so its scripts and styles are copied as they are.
        if name == "index.html":
            data = standalone_index(data.decode("utf-8")).encode("utf-8")
        yield name, data
    # rso.js, map.js, graph.js. Go also wrote plan.js, which index.html never loads.
    for doc_name, doc in assets.documents().items():
        yield f"{doc_name}.js", dumps_js(doc_name, doc).encode("utf-8")


def _add(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


def generate_zip(assets: Assets, filename: str | Path, frontend: Traversable | None = None) -> None:
    with zipfile.ZipFile(filename, "w") as zf:
        for name, data in site_files(assets, frontend):
            _add(zf, name, data)


def write_site(assets: Assets, directory: str | Path, frontend: Traversable | None = None) -> Path:
    """Write the standalone page to ``directory`` and return the path of its ``index.html``.

    The directory is replaced on every call, so it never mixes two plans. It holds plan data, so it
    is readable by the owner only (0700, files 0600). A directory that exists but does not look like
    a page written here (no ``index.html``) is left alone and is an error.
    """
    out = Path(directory)
    if out.exists():
        if not out.is_dir() or (any(out.iterdir()) and not (out / "index.html").is_file()):
            raise FileExistsError(f"{out} exists and is not a tfroam page; not replacing it")
        shutil.rmtree(out)
    out.mkdir(parents=True, mode=0o700)
    os.chmod(out, 0o700)  # mkdir's mode is masked by the umask
    for name, data in site_files(assets, frontend):
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    return out / "index.html"
