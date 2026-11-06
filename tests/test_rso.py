from __future__ import annotations

from pathlib import Path

import pytest

from rover.plan import Plan
from rover.rso import generate_resource_overview

from .golden_util import FIXTURES, GOLDEN, enter_fixture, load_js


def _run(fixture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Plan, dict[str, object]]:
    plan = Plan.from_json(load_js(GOLDEN / fixture / "plan.js"))
    working_dir = enter_fixture(fixture, tmp_path, monkeypatch)
    return plan, generate_resource_overview(plan, working_dir).to_json()


@pytest.mark.parametrize("fixture", FIXTURES)
def test_rso_matches_go(fixture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, got = _run(fixture, tmp_path, monkeypatch)
    assert got == load_js(GOLDEN / fixture / "rso.js")


@pytest.mark.parametrize("fixture", FIXTURES)
def test_plan_after_rso_matches_go_plan_js(fixture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The Go version mutates the plan while building the overview; plan.js reflects that.
    plan, _ = _run(fixture, tmp_path, monkeypatch)
    assert plan.to_json() == load_js(GOLDEN / fixture / "plan.js")


def _tiny_plan(**output: object) -> Plan:
    return Plan.from_json(
        {
            "format_version": "1.2",
            "configuration": {"root_module": {}},
            "output_changes": {"secret": {"actions": ["create"], "before": "old", "after": "hunter2", **output}},
        }
    )


def test_sensitive_outputs_are_masked_in_overview_and_plan(tmp_path: Path) -> None:
    plan = _tiny_plan(before_sensitive=True, after_sensitive=True)
    states = generate_resource_overview(plan, str(tmp_path)).to_json()["states"]
    change = states["secret"]["change"]
    assert change["before"] == "Sensitive Value" and change["after"] == "Sensitive Value"
    assert states["secret"]["type"] == "output"
    # Mutated in place, like Go: plan.js shows the placeholder too.
    assert plan.output_changes["secret"].after == "Sensitive Value"


def test_show_sensitive_keeps_values(tmp_path: Path) -> None:
    plan = _tiny_plan(after_sensitive=True)
    change = generate_resource_overview(plan, str(tmp_path), show_sensitive=True).to_json()["states"]["secret"][
        "change"
    ]
    assert change["after"] == "hunter2"


def test_non_bool_sensitive_marker_is_ignored(tmp_path: Path) -> None:
    # Go panics on a non-bool marker (object-shaped sensitivity); we leave the value alone.
    plan = _tiny_plan(after_sensitive={"a": True})
    change = generate_resource_overview(plan, str(tmp_path)).to_json()["states"]["secret"]["change"]
    assert change["after"] == "hunter2"


def test_missing_config_dir_and_modules_json_still_builds(tmp_path: Path) -> None:
    plan = _tiny_plan()
    rso = generate_resource_overview(plan, str(tmp_path / "does-not-exist")).to_json()
    assert "locations" not in rso
    assert "module" not in rso["configs"][""]  # no .tf data, as in Go
    assert rso["configs"][""]["module_config"] == {"module": {}}
    assert rso["states"][""] == {"change": {"before": None}, "type": "module"}


def test_plan_without_configuration_is_an_error() -> None:
    from rover.plan import PlanError

    with pytest.raises(PlanError):
        generate_resource_overview(Plan.from_json({"format_version": "1.2"}), ".")
