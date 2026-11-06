"""Module/file map builder (port of ``map.go``).

Groups each module's resources, variables, outputs, module calls and locals under
the ``.tf`` file they are declared in, as the frontend's file explorer expects.
"""

from __future__ import annotations

import logging
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

from .config import ProviderRequirement
from .plan import Expression
from .rso import (
    RESOURCE_TYPE_DATA,
    RESOURCE_TYPE_MODULE,
    RESOURCE_TYPE_OUTPUT,
    RESOURCE_TYPE_RESOURCE,
    ResourcesOverview,
)

log = logging.getLogger("rover")

RESOURCE_TYPE_FILE = "file"
RESOURCE_TYPE_LOCAL = "locals"
RESOURCE_TYPE_VARIABLE = "variable"
DEFAULT_FILE_NAME = "unknown file"
ACTION_REPLACE = "replace"

_CHILD_INDEX = re.compile(r"\[[^\[\]]*\]$")
_BRACKETS = re.compile(r"\[[^\[\]]*\]")


@dataclass
class MapResource:
    type: str
    name: str = ""
    line: int | None = None
    children: dict[str, MapResource] = field(default_factory=dict)
    change_action: str = ""
    required: bool | None = None
    sensitive: bool = False
    resource_type: str = ""
    source: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "name": self.name}
        if self.line is not None:  # pointer in Go: kept even when 0
            out["line"] = self.line
        if self.children:
            out["children"] = {k: v.to_json() for k, v in self.children.items()}
        if self.change_action:
            out["change_action"] = self.change_action
        if self.required is not None:
            out["required"] = self.required
        if self.sensitive:
            out["sensitive"] = True
        if self.resource_type:
            out["resource_type"] = self.resource_type
        if self.source:
            out["source"] = self.source
        return out


@dataclass
class Map:
    path: str
    required_core: list[str] = field(default_factory=list)
    required_providers: dict[str, ProviderRequirement] = field(default_factory=dict)
    root: dict[str, MapResource] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.path}
        if self.required_core:
            out["required_core"] = self.required_core
        if self.required_providers:
            out["required_providers"] = {k: v.to_json() for k, v in self.required_providers.items()}
        if self.root:
            out["root"] = {k: v.to_json() for k, v in self.root.items()}
        return out


def _base(filename: str) -> str:
    return posixpath.basename(filename.replace("\\", "/"))


def _change_action(actions: list[str]) -> str:
    if not actions:
        return ""
    return ACTION_REPLACE if len(actions) > 1 else actions[0]


def add_file_if_not_exists(module: MapResource, fname: str) -> None:
    if fname not in module.children:
        module.children[fname] = MapResource(
            type=RESOURCE_TYPE_FILE,
            name=fname,
            source=f"{module.source}/{fname}",  # module nodes have no source, so this is "/<fname>" like Go
        )


def generate_module_map(rso: ResourcesOverview, parent: MapResource, parent_module: str) -> None:
    states = rso.states
    configs = rso.configs

    prefix = f"{parent_module}." if parent_module else ""
    parent_config_id = _BRACKETS.sub("", parent_module)
    parent_config = configs.get(parent_config_id)
    parent_loaded = parent_config.module if parent_config is not None else None
    parent_configured = parent_loaded is not None
    parent_is_parent = states[parent_module].is_parent

    if parent_loaded is not None and not parent_is_parent:
        # Variables and outputs with file names and line numbers.
        for o_name, o in parent_loaded.outputs.items():
            fname = _base(o.pos.filename)
            oid = f"{prefix}output.{o_name}"
            add_file_if_not_exists(parent, fname)
            parent.children[fname].children[oid] = MapResource(
                type=RESOURCE_TYPE_OUTPUT, name=o_name, sensitive=o.sensitive, line=o.pos.line
            )

        for v_name, v in parent_loaded.variables.items():
            fname = _base(v.pos.filename)
            vid = f"{prefix}var.{v_name}"
            add_file_if_not_exists(parent, fname)
            parent.children[fname].children[vid] = MapResource(
                type=RESOURCE_TYPE_VARIABLE, name=v_name, required=v.required, line=v.pos.line
            )
    elif (
        parent_config is not None
        and parent_config.module_config is not None
        and parent_config.module_config.module is not None
        and not parent_is_parent
    ):
        # No configuration files: variables and outputs from the plan only.
        plan_module = parent_config.module_config.module
        for o_name, plan_output in plan_module.outputs.items():
            oid = f"{prefix}output.{o_name}"
            parent.children[oid] = MapResource(type=RESOURCE_TYPE_OUTPUT, name=o_name, sensitive=plan_output.sensitive)
        for v_name in plan_module.variables:
            vid = f"{prefix}var.{v_name}"
            parent.children[vid] = MapResource(type=RESOURCE_TYPE_VARIABLE, name=v_name)

    for rid, rs in states[parent_module].children.items():
        config_id = _BRACKETS.sub("", rid)
        entry = configs.get(config_id)
        configured = parent_loaded is not None and entry is not None

        re_ = MapResource(type=rs.type)
        re_.change_action = _change_action(states[rid].change.actions)

        if rs.type in (RESOURCE_TYPE_RESOURCE, RESOURCE_TYPE_DATA):
            assert entry is not None and entry.resource_config is not None, f"no configuration for {rid}"
            re_.resource_type = entry.resource_config.type
            re_.name = entry.resource_config.name

            for cr_name, cr in states[rid].children.items():
                tcr = MapResource(type=rs.type)
                if rs.type == RESOURCE_TYPE_DATA:
                    tcr.name = cr_name.removeprefix(f"{prefix}data.{re_.resource_type}.")
                else:
                    tcr.name = cr_name.removeprefix(f"{prefix}{re_.resource_type}.")
                tcr.change_action = _change_action(cr.change.actions)
                re_.children[cr_name] = tcr

            if configured:
                assert parent_loaded is not None
                ind = f"{re_.resource_type}.{re_.name}"
                if rs.type == RESOURCE_TYPE_DATA:
                    ind = f"data.{ind}"

                if rs.type == RESOURCE_TYPE_DATA and ind in parent_loaded.data_resources:
                    found = parent_loaded.data_resources[ind]
                    fname = _base(found.pos.filename)
                    re_.line = found.pos.line
                    add_file_if_not_exists(parent, fname)
                    parent.children[fname].children[rid] = re_
                elif rs.type == RESOURCE_TYPE_RESOURCE and ind in parent_loaded.managed_resources:
                    found = parent_loaded.managed_resources[ind]
                    fname = _base(found.pos.filename)
                    re_.line = found.pos.line
                    add_file_if_not_exists(parent, fname)
                    parent.children[fname].children[rid] = re_
                else:
                    add_file_if_not_exists(parent, DEFAULT_FILE_NAME)
                    parent.children[DEFAULT_FILE_NAME].children[rid] = re_
            else:
                parent.children[rid] = re_

        elif rs.type == RESOURCE_TYPE_MODULE:
            re_.name = rid.split(".")[-1]
            call_name = _BRACKETS.sub("", re_.name)
            call = parent_loaded.module_calls.get(call_name) if parent_loaded is not None else None

            if configured and not _CHILD_INDEX.search(rid) and call is not None:
                fname = _base(call.pos.filename)
                re_.line = call.pos.line
                add_file_if_not_exists(parent, fname)
                parent.children[fname].children[rid] = re_
            else:
                parent.children[rid] = re_

            generate_module_map(rso, re_, rid)

        # Locals referenced by this resource, module call or output.
        if entry is not None and not (re_.type == RESOURCE_TYPE_MODULE and _CHILD_INDEX.search(rid)):
            expressions: list[Expression] = []
            if re_.type == RESOURCE_TYPE_RESOURCE:
                assert entry.resource_config is not None
                expressions = list(entry.resource_config.expressions.values())
            elif re_.type == RESOURCE_TYPE_MODULE:
                assert entry.module_config is not None
                expressions = list(entry.module_config.expressions.values())
            elif re_.type == RESOURCE_TYPE_OUTPUT:
                assert entry.output_config is not None
                if entry.output_config.expression is not None:
                    expressions = [entry.output_config.expression]

            for expression in expressions:
                for reference in expression.references:
                    if not reference.startswith("local."):
                        continue
                    ref = MapResource(type=RESOURCE_TYPE_LOCAL, name=reference.removeprefix("local."))
                    local_id = f"{prefix}{reference}"
                    if parent_configured:
                        add_file_if_not_exists(parent, DEFAULT_FILE_NAME)
                        parent.children[DEFAULT_FILE_NAME].children[local_id] = ref
                    else:
                        parent.children[local_id] = ref


def generate_map(rso: ResourcesOverview) -> Map:
    log.info("Generating resource map...")

    root_module = MapResource(type=RESOURCE_TYPE_MODULE, name="", source="unknown")
    map_obj = Map(path="Rover Visualization", root=root_module.children)

    root_config = rso.configs[""].module
    if root_config is not None:
        root_module.source = root_config.path
        map_obj.path = root_config.path
        map_obj.required_providers = root_config.required_providers
        map_obj.required_core = root_config.required_core
        generate_module_map(rso, root_module, "")
    else:
        add_file_if_not_exists(root_module, DEFAULT_FILE_NAME)
        generate_module_map(rso, root_module.children[DEFAULT_FILE_NAME], "")

    return map_obj
