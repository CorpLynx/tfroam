"""Build ``Assets`` for a golden fixture (shared by export and server tests)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tfroam.assets import Assets
from tfroam.graph import generate_graph
from tfroam.map import generate_map
from tfroam.plan import Plan
from tfroam.rso import generate_resource_overview

from .golden_util import GOLDEN, enter_fixture, load_js


def build_assets(fixture: str, tmp_path: Path, monkeypatch: Any) -> Assets:
    plan = Plan.from_json(load_js(GOLDEN / fixture / "plan.js"))
    rso = generate_resource_overview(plan, enter_fixture(fixture, tmp_path, monkeypatch))
    map_obj = generate_map(rso)
    return Assets(plan=plan, rso=rso, map=map_obj, graph=generate_graph(rso, map_obj))
