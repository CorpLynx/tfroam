"""Helpers for reading Go-generated golden ``*.js`` files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GOLDEN = Path(__file__).parent / "golden"
FIXTURES = sorted(p.name for p in GOLDEN.iterdir() if p.is_dir())


def load_js(path: Path) -> Any:
    """Parse ``const name = {...}`` into its JSON value."""
    text = path.read_text(encoding="utf-8")
    return json.loads(text[text.index("=") + 1 :].strip())


def enter_fixture(fixture: str, tmp_path: Path, monkeypatch: Any) -> str:
    """chdir where the Go golden was captured and return the ``-workingDir`` it used."""
    fixtures = Path(__file__).parent / "fixtures"
    if fixture == "plan-only-test":
        (tmp_path / "empty").mkdir()  # a directory with no .tf files
        monkeypatch.chdir(tmp_path)
        return "empty"
    monkeypatch.chdir(fixtures / fixture)
    return "broken" if fixture == "noconfig-test" else "."
