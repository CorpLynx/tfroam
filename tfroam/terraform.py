"""Run Terraform and load plans (replaces main.go ``getPlan`` and terraform-exec).

Command lines and environment handling follow terraform-exec v0.15.0, which the
Go version uses.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from collections.abc import Sequence

from .plan import Plan, PlanError

log = logging.getLogger("tfroam")

_USER_AGENT = "HashiCorp-terraform-exec/0.15.0"


class TerraformError(RuntimeError):
    """A Terraform command failed; the message carries Terraform's stderr."""


def _build_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Caller's environment plus terraform-exec's fixed overrides.

    TF_VAR_* and TF_CLI_ARGS* pass through, so ``docker run --env-file`` keeps
    supplying credentials and variables.
    """
    env = dict(os.environ if base is None else base)
    env.setdefault("CHECKPOINT_DISABLE", "")
    previous = env.get("TF_APPEND_USER_AGENT", "")
    env["TF_APPEND_USER_AGENT"] = (
        _USER_AGENT if not previous or previous == _USER_AGENT else f"{previous} {_USER_AGENT}"
    )
    env["TF_LOG"] = ""
    env["TF_LOG_PATH"] = ""
    env["TF_IN_AUTOMATION"] = "1"
    env["TF_WORKSPACE"] = ""
    return env


class Terraform:
    """Thin wrapper over the Terraform binary (no shell, argument lists only)."""

    def __init__(self, working_dir: str | os.PathLike[str], tf_path: str | os.PathLike[str]) -> None:
        self.working_dir = str(working_dir)
        self.tf_path = str(tf_path)
        # Like terraform-exec's NewTerraform: the working directory must exist and a binary path
        # must be given, but the binary itself is only needed once a command runs
        # (so -planJSONPath works without Terraform installed).
        if not self.working_dir:
            raise TerraformError("Terraform cannot be initialised with empty workdir")
        if not os.path.exists(self.working_dir):
            raise TerraformError(
                f"error initialising Terraform with workdir {self.working_dir}: "
                f"stat {self.working_dir}: no such file or directory"
            )
        if not self.tf_path:
            raise TerraformError("NewTerraform: please supply the path to a Terraform executable using execPath")

    def _run(self, *args: str, allowed_codes: Sequence[int] = (0,)) -> str:
        try:
            proc = subprocess.run(
                [self.tf_path, *args],
                cwd=self.working_dir,
                env=_build_env(),
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise TerraformError(f"unable to run {self.tf_path}: {exc}") from exc
        if proc.returncode not in allowed_codes:
            raise TerraformError(f"exit status {proc.returncode}\n\n{proc.stderr}")
        return proc.stdout

    def init(self, backend_configs: Sequence[str] = ()) -> None:
        args = ["init", "-no-color", "-force-copy", "-input=false", "-backend=true", "-get=true", "-upgrade=true"]
        args += [f"-backend-config={bc}" for bc in backend_configs]
        self._run(*args)

    def workspace_select(self, name: str) -> None:
        self._run("workspace", "select", "-no-color", name)

    def plan(self, out: str, var_files: Sequence[str] = (), variables: Sequence[str] = ()) -> None:
        args = ["plan", "-no-color", "-input=false", "-detailed-exitcode", f"-out={out}"]
        args += [f"-var-file={vf}" for vf in var_files]
        args += ["-lock=true", "-parallelism=10", "-refresh=true"]
        for var in variables:
            args += ["-var", var]
        # -detailed-exitcode: 0 = no changes, 2 = changes present; both succeed.
        self._run(*args, allowed_codes=(0, 2))

    def show_plan_file(self, plan_path: str) -> Plan:
        out = self._run("show", "-json", "-no-color", plan_path)
        return parse_plan(out)


def parse_plan(text: str | bytes) -> Plan:
    try:
        return Plan.from_json(json.loads(text))
    except (json.JSONDecodeError, PlanError) as exc:
        raise PlanError(str(exc)) from exc


def load_plan_json(path: str | os.PathLike[str]) -> Plan:
    """Read a plan from a ``terraform show -json`` file."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise PlanError(f"Unable to read Plan ({path}): {exc}") from exc
    try:
        return parse_plan(data)
    except PlanError as exc:
        raise PlanError(f"Unable to read Plan ({path}): {exc}") from exc


def get_plan(
    *,
    working_dir: str,
    tf_path: str,
    plan_path: str = "",
    plan_json_path: str = "",
    workspace_name: str = "",
    tf_vars_files: Sequence[str] = (),
    tf_vars: Sequence[str] = (),
    tf_backend_configs: Sequence[str] = (),
) -> Plan:
    """Produce a Plan the same way the Go ``getPlan`` does (minus Terraform Cloud)."""
    tf = Terraform(working_dir, tf_path)

    if plan_path:
        log.info("Using provided plan...")
        try:
            return tf.show_plan_file(plan_path)
        except (TerraformError, PlanError) as exc:
            raise TerraformError(f"Unable to read Plan ({plan_path}): {exc}") from exc

    if plan_json_path:
        log.info("Using provided JSON plan...")
        return load_plan_json(plan_json_path)

    log.info("Initializing Terraform...")
    try:
        tf.init([bc for bc in tf_backend_configs if bc])
    except TerraformError as exc:
        raise TerraformError(f"Unable to initialize Terraform Plan: {exc}") from exc

    if workspace_name:
        log.info("Running in %s workspace...", workspace_name)
        try:
            tf.workspace_select(workspace_name)
        except TerraformError as exc:
            raise TerraformError(f"Unable to select workspace ({workspace_name}): {exc}") from exc

    log.info("Generating plan...")
    with tempfile.TemporaryDirectory(prefix="rover") as tmp_dir:  # removed on exit
        out = f"{tmp_dir}/roverplan-{int(time.time())}"
        try:
            tf.plan(out, [f for f in tf_vars_files if f], [v for v in tf_vars if v])
        except TerraformError as exc:
            raise TerraformError(f"Unable to run Plan: {exc}") from exc
        try:
            return tf.show_plan_file(out)
        except (TerraformError, PlanError) as exc:
            raise TerraformError(f"Unable to read Plan: {exc}") from exc
