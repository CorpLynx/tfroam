"""Sensitive values are redacted from every published document unless -showSensitive."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tfroam.assets import Assets
from tfroam.cli import main
from tfroam.graph import generate_graph
from tfroam.map import generate_map
from tfroam.plan import Plan
from tfroam.redact import SENSITIVE_PLACEHOLDER, redact_value
from tfroam.rso import generate_resource_overview

FIXTURE = Path(__file__).parent / "fixtures" / "sensitive-test"
S = SENSITIVE_PLACEHOLDER


def build(show_sensitive: bool = False) -> Assets:
    plan = Plan.from_json(json.loads((FIXTURE / "plan.json").read_text()))
    rso = generate_resource_overview(plan, str(FIXTURE), show_sensitive)
    map_obj = generate_map(rso)
    return Assets(plan=plan, rso=rso, map=map_obj, graph=generate_graph(rso, map_obj))


def _dump(value: Any) -> str:
    return json.dumps(value)


# --- the walker -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "marker", "want"),
    [
        ("x", True, S),
        ("x", False, "x"),
        ("x", None, "x"),
        (None, True, S),  # Go masked a null output the same way
        ({"a": 1, "b": 2}, {"a": True}, {"a": S, "b": 2}),
        ({"a": 1}, {}, {"a": 1}),
        ({"a": 1}, {"missing": True}, {"a": 1}),  # marker for an absent key adds nothing
        ({"a": {"b": "x", "c": "y"}}, {"a": {"b": True}}, {"a": {"b": S, "c": "y"}}),
        ({"a": {"b": "x"}}, {"a": True}, {"a": S}),  # sensitive at a level: whole subtree
        (["x", "y"], [False, True], ["x", S]),
        (["x", "y", "z"], [True], [S, "y", "z"]),  # shorter marker list: the rest is unmarked
        ([{"k": "x"}, {"k": "y"}], [{}, {"k": True}], [{"k": "x"}, {"k": S}]),
        # Marker shape doesn't match the value: conservative, replace the whole value.
        ("x", {"a": True}, S),
        ({"a": 1}, [True], S),
        (["x"], {"a": True}, S),
        # ... but a mismatched marker that marks nothing leaves the value alone.
        ("x", {"a": False}, "x"),
        ({"a": 1}, [], {"a": 1}),
        ("x", "yes", S),  # unexpected truthy scalar marker
    ],
)
def test_redact_value(value: Any, marker: Any, want: Any) -> None:
    assert redact_value(value, marker) == want


def test_redact_value_does_not_mutate_its_input() -> None:
    value = {"a": {"b": "x"}}
    redact_value(value, {"a": {"b": True}})
    assert value == {"a": {"b": "x"}}


# --- the fixture through the pipeline ---------------------------------------------------------


def test_no_secret_reaches_any_published_document() -> None:
    docs = build().documents()
    assert set(docs) == {"rso", "map", "graph"}
    for name, doc in docs.items():
        assert "SECRET" not in _dump(doc), name


def test_resource_changes_are_redacted_by_marker() -> None:
    states = build().rso.to_json()["states"]
    change = states["aws_db_instance.db"]["change"]
    for side in ("before", "after"):
        value = change[side]
        assert value["password"] == S
        assert value["username"] == "admin"
        assert value["tags"] == {"env": "prod", "token": S}
        assert value["ingress"] == [{"cidr": "10.0.0.0/8"}, {"cidr": S}]
    # Markers themselves are kept: the frontend shows which attributes are sensitive.
    assert change["after_sensitive"]["password"] is True

    whole = states["random_password.pw"]["change"]
    assert whole["before"] == S and whole["after"] == S


def test_state_values_reach_rso_redacted() -> None:
    # Without resource_changes, rso states keep the prior/planned state values as before/after.
    raw = json.loads((FIXTURE / "plan.json").read_text())
    del raw["resource_changes"]
    states = generate_resource_overview(Plan.from_json(raw), str(FIXTURE)).to_json()["states"]
    db = states["aws_db_instance.db"]["change"]
    assert db["before"]["password"] == S and db["before"]["tags"] == {"env": "prod", "token": S}
    assert db["after"]["ingress"] == [{"cidr": "10.0.0.0/8"}, {"cidr": S}]
    assert db["after"]["username"] == "admin"
    pw = states["random_password.pw"]["change"]
    assert pw["before"] == {"length": S, "result": S} and pw["after"] == {"length": S, "result": S}
    assert "SECRET" not in _dump(states)


def test_state_values_are_redacted_in_the_internal_plan() -> None:
    plan = build().plan
    assert plan.prior_state is not None and plan.prior_state.values is not None
    prior = plan.prior_state.values.root_module
    assert prior is not None
    db, pw = prior.resources
    assert db.values == {
        "username": "admin",
        "password": S,
        "tags": {"env": "prod", "token": S},
        "ingress": [{"cidr": "10.0.0.0/8"}, {"cidr": S}],
    }
    assert pw.values == {"length": S, "result": S}  # whole-value `true`
    assert plan.prior_state.values.outputs["conn"].value == S
    assert plan.planned_values is not None and plan.planned_values.outputs["conn"].value == S


def test_outputs_with_object_markers_are_redacted() -> None:
    states = build().rso.to_json()["states"]
    conn = states["conn"]["change"]
    assert conn["before"] == {"host": "db.example.com", "password": S}
    assert conn["after"] == {"host": "db.example.com", "password": S}
    assert states["conn"]["type"] == "output"
    assert states["region"]["change"]["after"] == "us-east-1"


def test_sensitive_variable_defaults_are_redacted() -> None:
    configs = build().rso.to_json()["configs"]
    assert configs["var.db_password"]["variable_config"]["default"] == S
    assert configs["var.region"]["variable_config"]["default"] == "us-east-1"
    tf_vars = configs[""]["module"]["variables"]  # from main.tf via tfroam/config.py
    assert tf_vars["db_password"]["default"] == S
    assert tf_vars["region"]["default"] == "us-east-1"
    assert "sensitive" not in tf_vars["db_password"]  # not a terraform-config-inspect field


def test_plan_variable_values_are_redacted_in_the_internal_plan() -> None:
    plan = build().plan
    assert plan.variables["db_password"] == {"value": S}
    assert plan.variables["region"] == {"value": "us-east-1"}
    assert "SECRET" not in _dump(plan.to_json())


def test_show_sensitive_disables_redaction() -> None:
    assets = build(show_sensitive=True)
    states = assets.rso.to_json()["states"]
    assert states["aws_db_instance.db"]["change"]["after"]["password"] == "hunter2-SECRET"
    assert states["random_password.pw"]["change"]["after"]["result"] == "whole-after-SECRET"
    assert states["conn"]["change"]["after"]["password"] == "output-after-SECRET"
    configs = assets.rso.to_json()["configs"]
    assert configs["var.db_password"]["variable_config"]["default"] == "var-default-SECRET"
    assert configs[""]["module"]["variables"]["db_password"]["default"] == "var-default-SECRET"


def _zip_text(zf: zipfile.ZipFile) -> str:
    return "".join(zf.read(n).decode("utf-8", "replace") for n in zf.namelist() if n.endswith((".js", ".html")))


def test_standalone_zip_has_no_plan_js_and_no_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    args = ["-workingDir", str(FIXTURE), "-planJSONPath", str(FIXTURE / "plan.json"), "-standalone", "true"]
    assert main(args) == 0
    with zipfile.ZipFile(tmp_path / "rover.zip") as zf:
        assert "plan.js" not in zf.namelist()
        assert "SECRET" not in _zip_text(zf)
        assert S in zf.read("rso.js").decode()


def test_standalone_zip_with_show_sensitive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    args = ["-workingDir", str(FIXTURE), "-planJSONPath", str(FIXTURE / "plan.json"), "-standalone", "true"]
    assert main([*args, "-showSensitive"]) == 0
    with zipfile.ZipFile(tmp_path / "rover.zip") as zf:
        assert "plan.js" not in zf.namelist()
        assert "hunter2-SECRET" in zf.read("rso.js").decode()


def test_child_module_state_and_sensitive_inputs_are_redacted(tmp_path: Path) -> None:
    resource = {
        "address": "module.m.x.y",
        "mode": "managed",
        "type": "x",
        "name": "y",
        "values": {"k": "child-SECRET", "ok": 1},
        "sensitive_values": {"k": True},
    }
    plan = Plan.from_json(
        {
            "format_version": "1.2",
            "planned_values": {"root_module": {"child_modules": [{"address": "module.m", "resources": [resource]}]}},
            "configuration": {
                "root_module": {
                    "module_calls": {
                        "m": {
                            "source": "./m",
                            "expressions": {"pw": {"constant_value": "input-SECRET"}, "n": {"constant_value": 2}},
                            "module": {
                                "variables": {"pw": {"sensitive": True}, "n": {"default": 1}},
                            },
                        }
                    }
                }
            },
        }
    )
    rso = generate_resource_overview(plan, str(tmp_path)).to_json()
    assert "SECRET" not in _dump(rso)
    assert rso["states"]["module.m.x.y"]["change"]["after"] == {"k": S, "ok": 1}
    exprs = rso["configs"]["module.m"]["module_config"]["expressions"]
    assert exprs == {"pw": {"constant_value": S}, "n": {"constant_value": 2}}
