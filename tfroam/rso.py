"""Resource overview builder (port of ``rso.go``).

Combines the plan's configuration, prior/planned state and changes with the
``.tf`` file data into the structure the frontend reads as ``rso``.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config
from .plan import (
    Change,
    ConfigModule,
    ConfigOutput,
    ConfigResource,
    ConfigVariable,
    ModuleCall,
    Plan,
    PlanError,
    StateModule,
)
from .redact import SENSITIVE_PLACEHOLDER as SENSITIVE_PLACEHOLDER  # re-exported: the Go name lived here
from .redact import redact_module, redact_plan

log = logging.getLogger("tfroam")

RESOURCE_TYPE_RESOURCE = "resource"
RESOURCE_TYPE_DATA = "data"
RESOURCE_TYPE_MODULE = "module"
RESOURCE_TYPE_OUTPUT = "output"

_CHILD_INDEX = re.compile(r"\[[^\[\]]*\]$")
_BRACKETS = re.compile(r"\[[^\[\]]*\]")


@dataclass
class StateOverview:
    change: Change = field(default_factory=Change)
    module: StateModule | None = None
    children: dict[str, StateOverview] = field(default_factory=dict)
    type: str = ""
    is_parent: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"change": self.change.to_json()}  # a struct: never omitted in Go
        if self.module is not None:
            out["module"] = self.module.to_json()
        if self.children:
            out["children"] = {k: v.to_json() for k, v in self.children.items()}
        if self.type:
            out["type"] = self.type
        if self.is_parent:
            out["isparent"] = True
        return out


@dataclass
class ConfigOverview:
    resource_config: ConfigResource | None = None
    module_config: ModuleCall | None = None
    variable_config: ConfigVariable | None = None
    output_config: ConfigOutput | None = None
    module: config.Module | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.resource_config is not None:
            out["resource_config"] = self.resource_config.to_json()
        if self.module_config is not None:
            out["module_config"] = self.module_config.to_json()
        if self.variable_config is not None:
            out["variable_config"] = self.variable_config.to_json()
        if self.output_config is not None:
            out["output_config"] = self.output_config.to_json()
        if self.module is not None:
            out["module"] = self.module.to_json()
        return out


@dataclass
class ResourcesOverview:
    locations: dict[str, str] = field(default_factory=dict)
    states: dict[str, StateOverview] = field(default_factory=dict)
    configs: dict[str, ConfigOverview] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.locations:
            out["locations"] = self.locations
        if self.states:
            out["states"] = {k: v.to_json() for k, v in self.states.items()}
        if self.configs:
            out["configs"] = {k: v.to_json() for k, v in self.configs.items()}
        return out


def populate_module_locations(module_json_file: str, working_dir: str, locations: dict[str, str]) -> None:
    """Read ``.terraform/modules/modules.json`` (if present) into ``locations``."""
    try:
        data = json.loads(Path(module_json_file).read_bytes())
    except OSError:
        log.info("No submodule configurations found...")
        return
    except ValueError:
        return  # Go ignores unmarshal errors
    modules = data.get("Modules") if isinstance(data, dict) else None
    for loc in modules or []:
        if isinstance(loc, dict):
            locations[str(loc.get("Key") or "")] = f"{config.to_posix(working_dir)}/{loc.get('Dir') or ''}"


def populate_configs(parent: str, parent_key: str, rso: ResourcesOverview, module: ConfigModule | None) -> None:
    if module is None:
        module = ConfigModule()
    rc = rso.configs
    prefix = f"{parent}." if parent else ""

    for name, variable in module.variables.items():
        rc.setdefault(f"{prefix}var.{name}", ConfigOverview()).variable_config = variable

    for name, output in module.outputs.items():
        rc.setdefault(f"{prefix}output.{name}", ConfigOverview()).output_config = output

    for resource in module.resources:
        rc.setdefault(f"{prefix}{resource.address}", ConfigOverview()).resource_config = resource
        rc.setdefault(parent, ConfigOverview())

    for module_name, call in module.module_calls.items():
        mn = f"module.{module_name}"
        if prefix:
            mn = f"{prefix}{mn}"
        entry = rc.setdefault(mn, ConfigOverview())

        child_key = module_name.removeprefix("module.")
        if parent_key:
            child_key = f"{parent_key}.{child_key}"

        child = config.load_module(rso.locations.get(child_key, ""))
        if not child.has_errors():
            entry.module = child
        else:
            log.info("Continuing without loading module from filesystem: %s", child_key)

        entry.module_config = call
        populate_configs(mn, child_key, rso, call.module)


def _resource_kind(mode: str) -> str:
    return RESOURCE_TYPE_DATA if mode == "data" else RESOURCE_TYPE_RESOURCE


def _new_module_state(is_parent: bool = False) -> StateOverview:
    return StateOverview(type=RESOURCE_TYPE_MODULE, is_parent=is_parent)


def populate_module_state(rso: ResourcesOverview, module: StateModule, prior: bool) -> None:
    rs = rso.states

    for rst in module.resources:
        rid = rst.address
        parent = module.address
        if rst.values is None:
            continue

        if rid not in rs:
            rs[rid] = StateOverview(type=_resource_kind(rst.mode))

        if parent not in rs:
            rs[parent] = _new_module_state()

        # Resource created with count or for_each: attach under a synthetic parent.
        if _CHILD_INDEX.search(rid):
            parent = _CHILD_INDEX.sub("", rid, count=1)
            if parent not in rs:
                rs[parent] = StateOverview(type=_resource_kind(rst.mode))
            rs[module.address].children[parent] = rs[parent]

        rs[parent].children[rid] = rs[rid]

        if prior:
            rs[rid].change.before = rst.values
        else:
            rs[rid].change.after = rst.values

    for child_module in module.child_modules:
        parent = module.address
        mid = child_module.address

        if parent not in rs:
            rs[parent] = _new_module_state()

        if _CHILD_INDEX.search(mid):
            parent = _CHILD_INDEX.sub("", mid, count=1)
            if parent not in rs:
                rs[parent] = _new_module_state(is_parent=True)
            rs[module.address].children[parent] = rs[parent]

        if rs[parent].module is None:
            rs[parent].module = module

        if mid not in rs:
            rs[mid] = _new_module_state()

        rs[mid].module = child_module
        rs[parent].children[mid] = rs[mid]

        populate_module_state(rso, child_module, prior)


def generate_resource_overview(plan: Plan, working_dir: str, show_sensitive: bool = False) -> ResourcesOverview:
    """Build the overview.

    Unless ``show_sensitive``, sensitive values are redacted in ``plan`` itself
    first (Go did this for outputs only) and in the ``.tf`` data loaded here,
    so nothing built from either carries them.
    """
    log.info("Generating resource overview...")

    if plan.configuration is None or plan.configuration.root_module is None:
        raise PlanError("plan has no configuration block")

    if not show_sensitive:
        redact_plan(plan)

    rso = ResourcesOverview()
    rc = rso.configs
    rs = rso.states

    populate_module_locations(str(Path(working_dir, ".terraform/modules/modules.json")), working_dir, rso.locations)

    rc[""] = ConfigOverview()
    root = config.load_module(working_dir)
    if not root.has_errors():
        rc[""].module = root
    else:
        log.info("Could not load configuration from: %s", working_dir)
        log.info("Continuing without configuration file data...")

    rc[""].module_config = ModuleCall(module=plan.configuration.root_module)

    populate_configs("", "", rso, plan.configuration.root_module)

    if plan.prior_state and plan.prior_state.values and plan.prior_state.values.root_module:
        populate_module_state(rso, plan.prior_state.values.root_module, True)

    if plan.planned_values and plan.planned_values.root_module:
        populate_module_state(rso, plan.planned_values.root_module, False)

    if "" not in rs:
        rs[""] = _new_module_state()

    for output_name, output in plan.output_changes.items():
        entry = rs.setdefault(output_name, StateOverview())
        entry.change = copy.copy(output)
        entry.type = RESOURCE_TYPE_OUTPUT

    for resource in plan.resource_changes:
        rid = resource.address
        config_id = _BRACKETS.sub("", rid)
        parent = resource.module_address

        if resource.change is None:
            continue

        if parent not in rs:
            rs[parent] = StateOverview()

        if rid not in rs:
            rs[rid] = StateOverview(type=_resource_kind(resource.mode))
            rs[parent].children[rid] = rs[rid]
        rs[rid].change = copy.copy(resource.change)

        if config_id not in rc:
            rc[config_id] = ConfigOverview(resource_config=ConfigResource(name=resource.name, type=resource.type))

    if not show_sensitive:
        for cfg in rc.values():
            if cfg.module is not None:
                redact_module(cfg.module)

    return rso
