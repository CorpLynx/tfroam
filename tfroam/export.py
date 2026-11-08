"""Standalone mode: write ``<name>.zip`` containing the UI and the data, openable from disk."""

from __future__ import annotations

import zipfile
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


def standalone_js(content: bytes) -> bytes:
    """Make webpack's public-path asset references relative."""
    return content.replace(b'r.p+"', b'"./')


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


def _add(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


def generate_zip(assets: Assets, filename: str | Path, frontend: Traversable | None = None) -> None:
    root = frontend if frontend is not None else dist()
    with zipfile.ZipFile(filename, "w") as zf:
        for name, item in _entries(root):
            data = item.read_bytes()
            if name == "index.html":
                data = standalone_index(data.decode("utf-8")).encode("utf-8")
            elif name.endswith(".js"):
                data = standalone_js(data)
            _add(zf, name, data)
        # rso.js, map.js, graph.js. Go also wrote plan.js, which index.html never loads.
        for doc_name, doc in assets.documents().items():
            _add(zf, f"{doc_name}.js", dumps_js(doc_name, doc).encode("utf-8"))
