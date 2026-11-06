from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rover.graph import generate_graph
from rover.map import generate_map
from rover.plan import Plan
from rover.rso import generate_resource_overview

from .golden_util import FIXTURES, GOLDEN, enter_fixture, load_js


def _by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Go iterates maps in random order, so list order is arbitrary: compare by id."""
    keyed = {item["data"]["id"]: item for item in items}
    assert len(keyed) == len(items), "duplicate ids"
    return keyed


@pytest.mark.parametrize("fixture", FIXTURES)
def test_graph_matches_go(fixture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = Plan.from_json(load_js(GOLDEN / fixture / "plan.js"))
    working_dir = enter_fixture(fixture, tmp_path, monkeypatch)
    rso = generate_resource_overview(plan, working_dir)
    got = generate_graph(rso, generate_map(rso)).to_json()
    want = load_js(GOLDEN / fixture / "graph.js")
    assert _by_id(got["nodes"]) == _by_id(want["nodes"])
    assert _by_id(got["edges"]) == _by_id(want["edges"])


def test_graph_output_order_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = Plan.from_json(load_js(GOLDEN / "for-each-test" / "plan.js"))
    working_dir = enter_fixture("for-each-test", tmp_path, monkeypatch)
    rso = generate_resource_overview(plan, working_dir)
    first = generate_graph(rso, generate_map(rso)).to_json()
    second = generate_graph(rso, generate_map(rso)).to_json()
    assert first == second


def _synthetic_graph(tmp_path: Path, references: list[str]) -> dict[str, Any]:
    plan = Plan.from_json(
        {
            "format_version": "1.2",
            "configuration": {
                "root_module": {
                    "resources": [
                        {
                            "address": "null_resource.a",
                            "mode": "managed",
                            "type": "null_resource",
                            "name": "a",
                            "expressions": {"triggers": {"references": references}},
                        },
                        {"address": "null_resource.b", "mode": "managed", "type": "null_resource", "name": "b"},
                    ]
                }
            },
            "resource_changes": [
                {
                    "address": f"null_resource.{n}",
                    "mode": "managed",
                    "type": "null_resource",
                    "name": n,
                    "change": {"actions": ["create"], "before": None},
                }
                for n in ("a", "b")
            ],
        }
    )
    rso = generate_resource_overview(plan, str(tmp_path))
    return generate_graph(rso, generate_map(rso)).to_json()


def test_edge_filtering_rules(tmp_path: Path) -> None:
    graph = _synthetic_graph(
        tmp_path,
        [
            "null_resource.b",  # whole-resource reference: kept
            "null_resource.b.id",  # attribute reference: dropped
            "each.key",  # dropped
            "data.t.d",  # data source, 3 parts: kept
            "data.t.d.attr",  # data source attribute: dropped
            "var.v",  # variable: kept (any length)
            "local.l",  # local: kept
        ],
    )
    targets = {e["data"]["target"]: e["data"]["gradient"] for e in graph["edges"]}
    assert targets == {
        "null_resource.b": "lightgray lightgray",
        "data.t.d": "lightgray #dc477d",
        "var.v": "lightgray #1d7ada",
        "local.l": "lightgray black",
    }


def test_terraform_workspace_gets_its_own_node(tmp_path: Path) -> None:
    graph = _synthetic_graph(tmp_path, ["terraform.workspace"])
    node = next(n for n in graph["nodes"] if n["data"]["id"] == "terraform.workspace")
    assert node == {
        "data": {
            "id": "terraform.workspace",
            "label": "terraform.workspace",
            "type": "locals",
            "parent": graph["nodes"][0]["data"]["id"],
        },
        "classes": "locals",
    }
    assert any(e["data"]["target"] == "terraform.workspace" for e in graph["edges"])
