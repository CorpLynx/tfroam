from __future__ import annotations

from pathlib import Path

import pytest

from rover.map import generate_map
from rover.plan import Plan
from rover.rso import generate_resource_overview

from .golden_util import FIXTURES, GOLDEN, enter_fixture, load_js


@pytest.mark.parametrize("fixture", FIXTURES)
def test_map_matches_go(fixture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = Plan.from_json(load_js(GOLDEN / fixture / "plan.js"))
    working_dir = enter_fixture(fixture, tmp_path, monkeypatch)
    rso = generate_resource_overview(plan, working_dir)
    assert generate_map(rso).to_json() == load_js(GOLDEN / fixture / "map.js")
