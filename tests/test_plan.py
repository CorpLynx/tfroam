from __future__ import annotations

import pytest

from rover.plan import Plan, PlanError

from .golden_util import FIXTURES, GOLDEN, load_js


@pytest.mark.parametrize("fixture", FIXTURES)
def test_plan_roundtrip_matches_go(fixture: str) -> None:
    raw = load_js(GOLDEN / fixture / "plan.js")
    assert Plan.from_json(raw).to_json() == raw


def test_unknown_fields_are_dropped() -> None:
    raw = {"format_version": "1.2", "applyable": True, "timestamp": "x", "errored": False}
    assert Plan.from_json(raw).to_json() == {"format_version": "1.2"}


def test_missing_or_unsupported_format_version() -> None:
    with pytest.raises(PlanError):
        Plan.from_json({})
    with pytest.raises(PlanError):
        Plan.from_json({"format_version": "2.0"})


def test_null_constant_value_is_omitted_like_go() -> None:
    raw = {
        "format_version": "1.2",
        "configuration": {"root_module": {"outputs": {"o": {"expression": {"constant_value": None}}}}},
    }
    out = Plan.from_json(raw).to_json()
    assert out["configuration"]["root_module"]["outputs"]["o"]["expression"] == {}
