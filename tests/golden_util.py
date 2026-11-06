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
