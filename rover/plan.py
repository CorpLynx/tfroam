"""Typed models for ``terraform show -json`` plans.

These mirror hashicorp/terraform-json v0.13.0, the version the Go original
uses, on purpose: fields that library does not know are dropped on load, and
``to_json`` applies Go's ``omitempty`` rules, so the serialized plan matches
what the Go version feeds to the frontend.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")

_FORMAT_VERSION_RE = re.compile(r"^(\d+)\.(\d+)")


class PlanError(ValueError):
    """Raised when a plan JSON document is invalid or unsupported."""


def _is_empty(value: Any) -> bool:
    """Go ``omitempty`` for concrete (non-interface) typed fields."""
    if value is None or value is False:
        return True
    if isinstance(value, (str, list, dict)):
        return len(value) == 0
    if isinstance(value, (int, float)):
        return value == 0
    return False


def _emit(out: dict[str, Any], key: str, value: Any, *, concrete: bool = True) -> None:
    """Add ``key`` unless Go would omit it.

    ``concrete`` fields (string, bool, slice, map) are omitted when empty; fields
    typed ``interface{}`` in Go are omitted only when nil.
    """
    if concrete:
        if not _is_empty(value):
            out[key] = value
    elif value is not None:
        out[key] = value


def _map(raw: Any, parse: Callable[[Any], T]) -> dict[str, T]:
    if not isinstance(raw, dict):
        return {}
    return {k: parse(v) for k, v in raw.items() if v is not None}


def _str_list(raw: Any) -> list[str]:
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _check_format_version(raw: dict[str, Any], what: str) -> str:
    version = raw.get("format_version") or ""
    if not version:
        raise PlanError(f"unexpected {what} input, format version is missing")
    match = _FORMAT_VERSION_RE.match(version)
    if not match:
        raise PlanError(f'invalid format version "{version}"')
    major = int(match.group(1))
    if major >= 2:
        raise PlanError(f'unsupported {what} format version: "{version}" does not satisfy ">= 0.1, < 2.0"')
    return str(version)


@dataclass
class Expression:
    constant_value: Any = None
    references: list[str] = field(default_factory=list)
    nested_blocks: list[dict[str, Expression]] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: Any) -> Expression:
        if isinstance(raw, list):
            blocks = [{k: cls.from_json(v) for k, v in b.items()} for b in raw if isinstance(b, dict)]
            return cls(nested_blocks=blocks)
        data = raw if isinstance(raw, dict) else {}
        refs = _str_list(data.get("references"))
        # terraform-json marks the value unknown when references are present.
        return cls(constant_value=None if refs else data.get("constant_value"), references=refs)

    def to_json(self) -> Any:
        if self.nested_blocks:
            return [{k: v.to_json() for k, v in b.items()} for b in self.nested_blocks]
        out: dict[str, Any] = {}
        _emit(out, "constant_value", self.constant_value, concrete=False)
        _emit(out, "references", self.references)
        return out


def _exprs(raw: Any) -> dict[str, Expression]:
    return _map(raw, Expression.from_json)


def _exprs_json(m: dict[str, Expression]) -> dict[str, Any]:
    return {k: v.to_json() for k, v in m.items()}


@dataclass
class Change:
    actions: list[str] = field(default_factory=list)
    before: Any = None
    after: Any = None
    after_unknown: Any = None
    before_sensitive: Any = None
    after_sensitive: Any = None

    @classmethod
    def from_json(cls, raw: Any) -> Change:
        d = raw if isinstance(raw, dict) else {}
        return cls(
            actions=_str_list(d.get("actions")),
            before=d.get("before"),
            after=d.get("after"),
            after_unknown=d.get("after_unknown"),
            before_sensitive=d.get("before_sensitive"),
            after_sensitive=d.get("after_sensitive"),
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _emit(out, "actions", self.actions)
        out["before"] = self.before  # no omitempty in Go: always present
        _emit(out, "after", self.after, concrete=False)
        _emit(out, "after_unknown", self.after_unknown, concrete=False)
        _emit(out, "before_sensitive", self.before_sensitive, concrete=False)
        _emit(out, "after_sensitive", self.after_sensitive, concrete=False)
        return out


@dataclass
class ResourceChange:
    address: str = ""
    module_address: str = ""
    mode: str = ""
    type: str = ""
    name: str = ""
    index: Any = None
    provider_name: str = ""
    deposed: str = ""
    change: Change | None = None

    @classmethod
    def from_json(cls, raw: Any) -> ResourceChange:
        d = raw if isinstance(raw, dict) else {}
        return cls(
            address=d.get("address") or "",
            module_address=d.get("module_address") or "",
            mode=d.get("mode") or "",
            type=d.get("type") or "",
            name=d.get("name") or "",
            index=d.get("index"),
            provider_name=d.get("provider_name") or "",
            deposed=d.get("deposed") or "",
            change=Change.from_json(d["change"]) if d.get("change") is not None else None,
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _emit(out, "address", self.address)
        _emit(out, "module_address", self.module_address)
        _emit(out, "mode", self.mode)
        _emit(out, "type", self.type)
        _emit(out, "name", self.name)
        _emit(out, "index", self.index, concrete=False)
        _emit(out, "provider_name", self.provider_name)
        _emit(out, "deposed", self.deposed)
        if self.change is not None:
            out["change"] = self.change.to_json()
        return out


@dataclass
class StateResource:
    address: str = ""
    mode: str = ""
    type: str = ""
    name: str = ""
    index: Any = None
    provider_name: str = ""
    schema_version: int = 0
    values: dict[str, Any] | None = None  # None = absent (Go nil map), {} = present but empty
    sensitive_values: Any = None
    depends_on: list[str] = field(default_factory=list)
    tainted: bool = False
    deposed_key: str = ""

    @classmethod
    def from_json(cls, raw: Any) -> StateResource:
        d = raw if isinstance(raw, dict) else {}
        values = d.get("values")
        return cls(
            address=d.get("address") or "",
            mode=d.get("mode") or "",
            type=d.get("type") or "",
            name=d.get("name") or "",
            index=d.get("index"),
            provider_name=d.get("provider_name") or "",
            schema_version=int(d.get("schema_version") or 0),
            values=values if isinstance(values, dict) else None,
            sensitive_values=d.get("sensitive_values"),
            depends_on=_str_list(d.get("depends_on")),
            tainted=bool(d.get("tainted")),
            deposed_key=d.get("deposed_key") or "",
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _emit(out, "address", self.address)
        _emit(out, "mode", self.mode)
        _emit(out, "type", self.type)
        _emit(out, "name", self.name)
        _emit(out, "index", self.index, concrete=False)
        _emit(out, "provider_name", self.provider_name)
        out["schema_version"] = self.schema_version
        _emit(out, "values", self.values)
        _emit(out, "sensitive_values", self.sensitive_values, concrete=False)
        _emit(out, "depends_on", self.depends_on)
        _emit(out, "tainted", self.tainted)
        _emit(out, "deposed_key", self.deposed_key)
        return out


@dataclass
class StateModule:
    resources: list[StateResource] = field(default_factory=list)
    address: str = ""
    child_modules: list[StateModule] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: Any) -> StateModule:
        d = raw if isinstance(raw, dict) else {}
        return cls(
            resources=[StateResource.from_json(r) for r in d.get("resources") or []],
            address=d.get("address") or "",
            child_modules=[cls.from_json(m) for m in d.get("child_modules") or []],
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.resources:
            out["resources"] = [r.to_json() for r in self.resources]
        _emit(out, "address", self.address)
        if self.child_modules:
            out["child_modules"] = [m.to_json() for m in self.child_modules]
        return out


@dataclass
class StateOutput:
    sensitive: bool = False
    value: Any = None

    @classmethod
    def from_json(cls, raw: Any) -> StateOutput:
        d = raw if isinstance(raw, dict) else {}
        return cls(sensitive=bool(d.get("sensitive")), value=d.get("value"))

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"sensitive": self.sensitive}
        _emit(out, "value", self.value, concrete=False)
        return out


@dataclass
class StateValues:
    outputs: dict[str, StateOutput] = field(default_factory=dict)
    root_module: StateModule | None = None

    @classmethod
    def from_json(cls, raw: Any) -> StateValues:
        d = raw if isinstance(raw, dict) else {}
        return cls(
            outputs=_map(d.get("outputs"), StateOutput.from_json),
            root_module=StateModule.from_json(d["root_module"]) if d.get("root_module") is not None else None,
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.outputs:
            out["outputs"] = {k: v.to_json() for k, v in self.outputs.items()}
        if self.root_module is not None:
            out["root_module"] = self.root_module.to_json()
        return out


@dataclass
class State:
    format_version: str = ""
    terraform_version: str = ""
    values: StateValues | None = None

    @classmethod
    def from_json(cls, raw: Any) -> State:
        d = raw if isinstance(raw, dict) else {}
        return cls(
            format_version=_check_format_version(d, "state"),
            terraform_version=d.get("terraform_version") or "",
            values=StateValues.from_json(d["values"]) if d.get("values") is not None else None,
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _emit(out, "format_version", self.format_version)
        _emit(out, "terraform_version", self.terraform_version)
        if self.values is not None:
            out["values"] = self.values.to_json()
        return out


@dataclass
class ProviderConfig:
    name: str = ""
    alias: str = ""
    module_address: str = ""
    expressions: dict[str, Expression] = field(default_factory=dict)
    version_constraint: str = ""

    @classmethod
    def from_json(cls, raw: Any) -> ProviderConfig:
        d = raw if isinstance(raw, dict) else {}
        return cls(
            name=d.get("name") or "",
            alias=d.get("alias") or "",
            module_address=d.get("module_address") or "",
            expressions=_exprs(d.get("expressions")),
            version_constraint=d.get("version_constraint") or "",
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _emit(out, "name", self.name)
        _emit(out, "alias", self.alias)
        _emit(out, "module_address", self.module_address)
        if self.expressions:
            out["expressions"] = _exprs_json(self.expressions)
        _emit(out, "version_constraint", self.version_constraint)
        return out


@dataclass
class ConfigOutput:
    sensitive: bool = False
    expression: Expression | None = None
    description: str = ""
    depends_on: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: Any) -> ConfigOutput:
        d = raw if isinstance(raw, dict) else {}
        return cls(
            sensitive=bool(d.get("sensitive")),
            expression=Expression.from_json(d["expression"]) if d.get("expression") is not None else None,
            description=d.get("description") or "",
            depends_on=_str_list(d.get("depends_on")),
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _emit(out, "sensitive", self.sensitive)
        if self.expression is not None:
            out["expression"] = self.expression.to_json()
        _emit(out, "description", self.description)
        _emit(out, "depends_on", self.depends_on)
        return out


@dataclass
class ConfigProvisioner:
    type: str = ""
    expressions: dict[str, Expression] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: Any) -> ConfigProvisioner:
        d = raw if isinstance(raw, dict) else {}
        return cls(type=d.get("type") or "", expressions=_exprs(d.get("expressions")))

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _emit(out, "type", self.type)
        if self.expressions:
            out["expressions"] = _exprs_json(self.expressions)
        return out


@dataclass
class ConfigResource:
    address: str = ""
    mode: str = ""
    type: str = ""
    name: str = ""
    provider_config_key: str = ""
    provisioners: list[ConfigProvisioner] = field(default_factory=list)
    expressions: dict[str, Expression] = field(default_factory=dict)
    schema_version: int = 0
    count_expression: Expression | None = None
    for_each_expression: Expression | None = None
    depends_on: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: Any) -> ConfigResource:
        d = raw if isinstance(raw, dict) else {}
        return cls(
            address=d.get("address") or "",
            mode=d.get("mode") or "",
            type=d.get("type") or "",
            name=d.get("name") or "",
            provider_config_key=d.get("provider_config_key") or "",
            provisioners=[ConfigProvisioner.from_json(p) for p in d.get("provisioners") or []],
            expressions=_exprs(d.get("expressions")),
            schema_version=int(d.get("schema_version") or 0),
            count_expression=Expression.from_json(d["count_expression"])
            if d.get("count_expression") is not None
            else None,
            for_each_expression=Expression.from_json(d["for_each_expression"])
            if d.get("for_each_expression") is not None
            else None,
            depends_on=_str_list(d.get("depends_on")),
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _emit(out, "address", self.address)
        _emit(out, "mode", self.mode)
        _emit(out, "type", self.type)
        _emit(out, "name", self.name)
        _emit(out, "provider_config_key", self.provider_config_key)
        if self.provisioners:
            out["provisioners"] = [p.to_json() for p in self.provisioners]
        if self.expressions:
            out["expressions"] = _exprs_json(self.expressions)
        out["schema_version"] = self.schema_version
        if self.count_expression is not None:
            out["count_expression"] = self.count_expression.to_json()
        if self.for_each_expression is not None:
            out["for_each_expression"] = self.for_each_expression.to_json()
        _emit(out, "depends_on", self.depends_on)
        return out


@dataclass
class ConfigVariable:
    default: Any = None
    description: str = ""
    sensitive: bool = False

    @classmethod
    def from_json(cls, raw: Any) -> ConfigVariable:
        d = raw if isinstance(raw, dict) else {}
        return cls(
            default=d.get("default"),
            description=d.get("description") or "",
            sensitive=bool(d.get("sensitive")),
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _emit(out, "default", self.default, concrete=False)
        _emit(out, "description", self.description)
        _emit(out, "sensitive", self.sensitive)
        return out


@dataclass
class ModuleCall:
    source: str = ""
    expressions: dict[str, Expression] = field(default_factory=dict)
    count_expression: Expression | None = None
    for_each_expression: Expression | None = None
    module: ConfigModule | None = None
    version_constraint: str = ""
    depends_on: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: Any) -> ModuleCall:
        d = raw if isinstance(raw, dict) else {}
        return cls(
            source=d.get("source") or "",
            expressions=_exprs(d.get("expressions")),
            count_expression=Expression.from_json(d["count_expression"])
            if d.get("count_expression") is not None
            else None,
            for_each_expression=Expression.from_json(d["for_each_expression"])
            if d.get("for_each_expression") is not None
            else None,
            module=ConfigModule.from_json(d["module"]) if d.get("module") is not None else None,
            version_constraint=d.get("version_constraint") or "",
            depends_on=_str_list(d.get("depends_on")),
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _emit(out, "source", self.source)
        if self.expressions:
            out["expressions"] = _exprs_json(self.expressions)
        if self.count_expression is not None:
            out["count_expression"] = self.count_expression.to_json()
        if self.for_each_expression is not None:
            out["for_each_expression"] = self.for_each_expression.to_json()
        if self.module is not None:
            out["module"] = self.module.to_json()
        _emit(out, "version_constraint", self.version_constraint)
        _emit(out, "depends_on", self.depends_on)
        return out


@dataclass
class ConfigModule:
    outputs: dict[str, ConfigOutput] = field(default_factory=dict)
    resources: list[ConfigResource] = field(default_factory=list)
    module_calls: dict[str, ModuleCall] = field(default_factory=dict)
    variables: dict[str, ConfigVariable] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: Any) -> ConfigModule:
        d = raw if isinstance(raw, dict) else {}
        return cls(
            outputs=_map(d.get("outputs"), ConfigOutput.from_json),
            resources=[ConfigResource.from_json(r) for r in d.get("resources") or []],
            module_calls=_map(d.get("module_calls"), ModuleCall.from_json),
            variables=_map(d.get("variables"), ConfigVariable.from_json),
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.outputs:
            out["outputs"] = {k: v.to_json() for k, v in self.outputs.items()}
        if self.resources:
            out["resources"] = [r.to_json() for r in self.resources]
        if self.module_calls:
            out["module_calls"] = {k: v.to_json() for k, v in self.module_calls.items()}
        if self.variables:
            out["variables"] = {k: v.to_json() for k, v in self.variables.items()}
        return out


@dataclass
class Config:
    provider_configs: dict[str, ProviderConfig] = field(default_factory=dict)
    root_module: ConfigModule | None = None

    @classmethod
    def from_json(cls, raw: Any) -> Config:
        d = raw if isinstance(raw, dict) else {}
        return cls(
            provider_configs=_map(d.get("provider_config"), ProviderConfig.from_json),
            root_module=ConfigModule.from_json(d["root_module"]) if d.get("root_module") is not None else None,
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.provider_configs:
            out["provider_config"] = {k: v.to_json() for k, v in self.provider_configs.items()}
        if self.root_module is not None:
            out["root_module"] = self.root_module.to_json()
        return out


@dataclass
class Plan:
    format_version: str = ""
    terraform_version: str = ""
    variables: dict[str, Any] = field(default_factory=dict)
    planned_values: StateValues | None = None
    resource_changes: list[ResourceChange] = field(default_factory=list)
    output_changes: dict[str, Change] = field(default_factory=dict)
    prior_state: State | None = None
    configuration: Config | None = None

    @classmethod
    def from_json(cls, raw: Any) -> Plan:
        if not isinstance(raw, dict):
            raise PlanError("plan JSON must be an object")
        version = _check_format_version(raw, "plan")
        variables = {
            k: ({"value": v["value"]} if isinstance(v, dict) and v.get("value") is not None else {})
            for k, v in (raw.get("variables") or {}).items()
            if v is not None
        }
        return cls(
            format_version=version,
            terraform_version=raw.get("terraform_version") or "",
            variables=variables,
            planned_values=StateValues.from_json(raw["planned_values"])
            if raw.get("planned_values") is not None
            else None,
            resource_changes=[ResourceChange.from_json(r) for r in raw.get("resource_changes") or []],
            output_changes=_map(raw.get("output_changes"), Change.from_json),
            prior_state=State.from_json(raw["prior_state"]) if raw.get("prior_state") is not None else None,
            configuration=Config.from_json(raw["configuration"]) if raw.get("configuration") is not None else None,
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _emit(out, "format_version", self.format_version)
        _emit(out, "terraform_version", self.terraform_version)
        if self.variables:
            out["variables"] = self.variables
        if self.planned_values is not None:
            out["planned_values"] = self.planned_values.to_json()
        if self.resource_changes:
            out["resource_changes"] = [r.to_json() for r in self.resource_changes]
        if self.output_changes:
            out["output_changes"] = {k: v.to_json() for k, v in self.output_changes.items()}
        if self.prior_state is not None:
            out["prior_state"] = self.prior_state.to_json()
        if self.configuration is not None:
            out["configuration"] = self.configuration.to_json()
        return out
