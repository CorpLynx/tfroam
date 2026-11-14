"""Run Terraform and load plans (replaces main.go ``getPlan`` and terraform-exec).

Command lines and environment handling follow terraform-exec v0.15.0, which the
Go version uses, except that ``init`` defaults to ``-upgrade=false`` and does not
pass ``-force-copy``, and ``plan -lock``/``-refresh`` are configurable
(see PORTING_NOTES.md, "CLI and Terraform runner changes").
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from typing import IO, TextIO

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
    """Thin wrapper over the Terraform binary (no shell, argument lists only).

    With ``verbose``, the output of ``init``, ``workspace select`` and ``plan`` is copied live to
    ``stream`` (``sys.stderr`` when not given) as well as captured. ``show -json`` is never streamed.
    """

    def __init__(
        self,
        working_dir: str | os.PathLike[str],
        tf_path: str | os.PathLike[str],
        *,
        verbose: bool = False,
        stream: TextIO | None = None,
    ) -> None:
        self.working_dir = str(working_dir)
        self.tf_path = str(tf_path)
        self.verbose = verbose
        self.stream = stream
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

    def _run(self, *args: str, allowed_codes: Sequence[int] = (0,), stream: bool = False) -> str:
        if stream:
            returncode, stdout, stderr = self._run_streaming(list(args))
        else:
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
            returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        if returncode not in allowed_codes:
            raise TerraformError(f"exit status {returncode}\n\n{stderr}")
        return stdout

    def _run_streaming(self, args: list[str]) -> tuple[int, str, str]:
        """Run Terraform, copying stdout and stderr line by line to the stream while capturing both."""
        target = self.stream if self.stream is not None else sys.stderr
        write_lock = threading.Lock()
        captured: dict[str, list[str]] = {"stdout": [], "stderr": []}

        def pump(pipe: IO[str], key: str) -> None:
            for line in pipe:
                captured[key].append(line)
                with write_lock:
                    target.write(line)
                    target.flush()

        try:
            proc = subprocess.Popen(
                [self.tf_path, *args],
                cwd=self.working_dir,
                env=_build_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )
        except OSError as exc:
            raise TerraformError(f"unable to run {self.tf_path}: {exc}") from exc
        assert proc.stdout is not None and proc.stderr is not None
        pumps = [
            threading.Thread(target=pump, args=(proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=pump, args=(proc.stderr, "stderr"), daemon=True),
        ]
        try:
            for t in pumps:
                t.start()
            for t in pumps:
                t.join()
            returncode = proc.wait()
        except BaseException:  # e.g. Ctrl-C: don't leave Terraform running, like subprocess.run
            proc.kill()
            proc.wait()
            raise
        finally:
            proc.stdout.close()
            proc.stderr.close()
        return returncode, "".join(captured["stdout"]), "".join(captured["stderr"])

    def init(self, backend_configs: Sequence[str] = (), *, upgrade: bool = False) -> None:
        # No -force-copy: if the backend changed, Terraform (with -input=false) fails with its own
        # message instead of silently migrating state. -upgrade=false keeps .terraform.lock.hcl as is.
        args = ["init", "-no-color", "-input=false", "-backend=true", "-get=true", f"-upgrade={_flag(upgrade)}"]
        args += [f"-backend-config={bc}" for bc in backend_configs]
        self._run(*args, stream=self.verbose)

    def workspace_select(self, name: str) -> None:
        self._run("workspace", "select", "-no-color", name, stream=self.verbose)

    def plan(
        self,
        out: str,
        var_files: Sequence[str] = (),
        variables: Sequence[str] = (),
        *,
        lock: bool = True,
        refresh: bool = True,
    ) -> None:
        args = ["plan", "-no-color", "-input=false", "-detailed-exitcode", f"-out={out}"]
        args += [f"-var-file={vf}" for vf in var_files]
        args += [f"-lock={_flag(lock)}", "-parallelism=10", f"-refresh={_flag(refresh)}"]
        for var in variables:
            args += ["-var", var]
        # -detailed-exitcode: 0 = no changes, 2 = changes present; both succeed.
        self._run(*args, allowed_codes=(0, 2), stream=self.verbose)

    def show_plan_file(self, plan_path: str) -> Plan:
        out = self._run("show", "-json", "-no-color", plan_path)  # JSON on stdout: always captured
        return parse_plan(out)


def _flag(value: bool) -> str:
    return "true" if value else "false"


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
    tf_upgrade: bool = False,
    tf_lock: bool = True,
    tf_refresh: bool = True,
    verbose: bool = False,
) -> Plan:
    """Produce a Plan the same way the Go ``getPlan`` does (minus Terraform Cloud).

    ``tf_upgrade`` sets ``init -upgrade``; ``tf_lock`` and ``tf_refresh`` set ``plan -lock`` and
    ``-refresh``; ``verbose`` streams Terraform's output to stderr.
    """
    tf = Terraform(working_dir, tf_path, verbose=verbose)

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
        tf.init([bc for bc in tf_backend_configs if bc], upgrade=tf_upgrade)
    except TerraformError as exc:
        raise TerraformError(f"Unable to initialize Terraform Plan: {exc}") from exc

    if workspace_name:
        log.info("Running in %s workspace...", workspace_name)
        try:
            tf.workspace_select(workspace_name)
        except TerraformError as exc:
            raise TerraformError(f"Unable to select workspace ({workspace_name}): {exc}") from exc

    log.info("Generating plan...")
    with tempfile.TemporaryDirectory(prefix="tfroam") as tmp_dir:  # removed on exit
        out = f"{tmp_dir}/tfroamplan-{int(time.time())}"
        try:
            tf.plan(out, [f for f in tf_vars_files if f], [v for v in tf_vars if v], lock=tf_lock, refresh=tf_refresh)
        except TerraformError as exc:
            raise TerraformError(f"Unable to run Plan: {exc}") from exc
        try:
            return tf.show_plan_file(out)
        except (TerraformError, PlanError) as exc:
            raise TerraformError(f"Unable to read Plan: {exc}") from exc
