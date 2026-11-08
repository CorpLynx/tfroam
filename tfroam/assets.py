"""Run the pipeline (plan -> rso -> map -> graph) and serialize its outputs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .graph import Graph, filter_graph, generate_graph
from .map import Map, generate_map
from .plan import Plan, PlanError
from .rso import ResourcesOverview, generate_resource_overview
from .terraform import TerraformError, get_plan

DOCUMENT_NAMES = ("plan", "rso", "map", "graph")


class RoverError(RuntimeError):
    """A user-facing failure; the CLI prints the message and exits non-zero."""


@dataclass
class Assets:
    plan: Plan
    rso: ResourcesOverview
    map: Map
    graph: Graph

    def documents(self) -> dict[str, Any]:
        """The four JSON documents the frontend reads, keyed by name."""
        return {
            "plan": self.plan.to_json(),
            "rso": self.rso.to_json(),
            "map": self.map.to_json(),
            "graph": self.graph.to_json(),
        }


def generate_assets(
    *,
    working_dir: str = ".",
    tf_path: str = "/bin/terraform",
    plan_path: str = "",
    plan_json_path: str = "",
    workspace_name: str = "",
    tf_vars_files: Sequence[str] = (),
    tf_vars: Sequence[str] = (),
    tf_backend_configs: Sequence[str] = (),
    show_sensitive: bool = False,
    hide: Sequence[str] = (),
    tf_upgrade: bool = False,
    tf_lock: bool = True,
    tf_refresh: bool = True,
    verbose: bool = False,
) -> Assets:
    try:
        plan = get_plan(
            working_dir=working_dir,
            tf_path=tf_path,
            plan_path=plan_path,
            plan_json_path=plan_json_path,
            workspace_name=workspace_name,
            tf_vars_files=tf_vars_files,
            tf_vars=tf_vars,
            tf_backend_configs=tf_backend_configs,
            tf_upgrade=tf_upgrade,
            tf_lock=tf_lock,
            tf_refresh=tf_refresh,
            verbose=verbose,
        )
    except (TerraformError, PlanError) as exc:
        raise RoverError(f"Unable to parse Plan: {exc}") from exc

    try:
        rso = generate_resource_overview(plan, working_dir, show_sensitive)
    except PlanError as exc:
        raise RoverError(str(exc)) from exc
    map_obj = generate_map(rso)
    try:
        graph = filter_graph(generate_graph(rso, map_obj), hide)
    except ValueError as exc:
        raise RoverError(str(exc)) from exc
    return Assets(plan=plan, rso=rso, map=map_obj, graph=graph)


def dumps_json(value: Any) -> str:
    """Compact JSON with sorted keys (Go sorts map keys)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def dumps_js(name: str, value: Any) -> str:
    """``const <name> = {...}``: JSON as a script the page loads without CORS issues."""
    return f"const {name} = {dumps_json(value)}"
