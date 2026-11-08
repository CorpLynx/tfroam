"""Hide sensitive values before any document is built (not in the Go version).

Terraform marks sensitive data in the plan with marker trees that mirror the
value they describe (``before_sensitive``, ``after_sensitive``,
``sensitive_values``): ``true`` marks the whole (sub)value, an object or list
marks attributes or elements inside it, and anything falsy marks nothing. Every
marked value is replaced with :data:`SENSITIVE_PLACEHOLDER`, the string the Go
version already used for sensitive outputs.
"""

from __future__ import annotations

from typing import Any

from . import config
from .plan import Change, ConfigModule, Plan, StateModule, StateValues

SENSITIVE_PLACEHOLDER = "Sensitive Value"


def _marks_anything(marker: Any) -> bool:
    """Whether ``marker`` marks any part of a value as sensitive."""
    if isinstance(marker, dict):
        return any(_marks_anything(m) for m in marker.values())
    if isinstance(marker, list):
        return any(_marks_anything(m) for m in marker)
    return bool(marker)  # True, or an unexpected truthy scalar: treat as sensitive


def redact_value(value: Any, marker: Any) -> Any:
    """Return a copy of ``value`` with every part ``marker`` marks replaced.

    Unmarked parts are kept (and missing keys are not added). When the marker
    does not match the value's shape (an object marker on a string, a list
    marker on an object, an unexpected scalar), the whole value is replaced if
    the marker marks anything at all.
    """
    if isinstance(marker, dict) and isinstance(value, dict):
        return {k: redact_value(v, marker.get(k)) for k, v in value.items()}
    if isinstance(marker, list) and isinstance(value, list):
        return [redact_value(v, marker[i] if i < len(marker) else None) for i, v in enumerate(value)]
    return SENSITIVE_PLACEHOLDER if _marks_anything(marker) else value


def _redact_change(change: Change) -> None:
    change.before = redact_value(change.before, change.before_sensitive)
    change.after = redact_value(change.after, change.after_sensitive)


def _redact_state_module(module: StateModule | None) -> None:
    if module is None:
        return
    for resource in module.resources:
        if resource.values is None:
            continue
        marker = resource.sensitive_values
        if not isinstance(marker, dict) and _marks_anything(marker):
            # Whole object sensitive: keep the dict (rso skips resources without values).
            resource.values = {k: SENSITIVE_PLACEHOLDER for k in resource.values}
        else:
            resource.values = redact_value(resource.values, marker)
    for child in module.child_modules:
        _redact_state_module(child)


def _redact_state_values(values: StateValues | None) -> None:
    if values is None:
        return
    for output in values.outputs.values():
        if output.sensitive and output.value is not None:
            output.value = SENSITIVE_PLACEHOLDER
    _redact_state_module(values.root_module)


def _redact_config_module(module: ConfigModule | None) -> None:
    if module is None:
        return
    for variable in module.variables.values():
        if variable.sensitive and variable.default is not None:
            variable.default = SENSITIVE_PLACEHOLDER
    for call in module.module_calls.values():
        child_vars = call.module.variables if call.module is not None else {}
        for name, expr in call.expressions.items():
            # A literal passed to a sensitive variable of the child module.
            if name in child_vars and child_vars[name].sensitive and expr.constant_value is not None:
                expr.constant_value = SENSITIVE_PLACEHOLDER
        _redact_config_module(call.module)


def redact_plan(plan: Plan) -> None:
    """Redact ``plan`` in place: resource and output changes, prior and planned
    state, sensitive variable defaults and values."""
    for resource in plan.resource_changes:
        if resource.change is not None:
            _redact_change(resource.change)
    for output in plan.output_changes.values():
        _redact_change(output)
    if plan.prior_state is not None:
        _redact_state_values(plan.prior_state.values)
    _redact_state_values(plan.planned_values)

    root = plan.configuration.root_module if plan.configuration is not None else None
    _redact_config_module(root)
    root_vars = root.variables if root is not None else {}
    for name, entry in plan.variables.items():
        if name in root_vars and root_vars[name].sensitive and isinstance(entry, dict) and "value" in entry:
            entry["value"] = SENSITIVE_PLACEHOLDER


def redact_module(module: config.Module) -> None:
    """Redact, in place, the default of every ``sensitive = true`` variable read from ``.tf`` files."""
    for variable in module.variables.values():
        if variable.sensitive and variable.default is not None:
            variable.default = SENSITIVE_PLACEHOLDER
