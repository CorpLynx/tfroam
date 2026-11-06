from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any

import pytest

from rover.plan import PlanError
from rover.terraform import Terraform, TerraformError, get_plan, load_plan_json

from .golden_util import FIXTURES, GOLDEN, load_js

FAKE = """#!/usr/bin/env python3
import json, os, sys
log = os.environ["FAKE_TF_LOG"]
with open(log, "a") as fh:
    fh.write(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd(),
        "env": {k: v for k, v in os.environ.items() if k.startswith(("TF_", "AWS_"))}}) + "\\n")
cmd = sys.argv[1]
if os.environ.get("FAKE_TF_FAIL") == cmd:
    sys.stderr.write("Error: boom from " + cmd + "\\n"); sys.exit(1)
if cmd == "plan":
    out = [a for a in sys.argv if a.startswith("-out=")][0][5:]
    open(out, "w").write("planfile"); sys.exit(2)
if cmd == "show":
    sys.stdout.write(open(os.environ["FAKE_TF_PLAN"]).read())
"""


@pytest.fixture
def fake_tf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    binary = tmp_path / "terraform"
    binary.write_text(FAKE)
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    plan = tmp_path / "plan.json"
    src = (GOLDEN / "simple-test" / "plan.js").read_text()
    plan.write_text(src[src.index("=") + 1 :])
    monkeypatch.setenv("FAKE_TF_LOG", str(log))
    monkeypatch.setenv("FAKE_TF_PLAN", str(plan))
    return binary, log


def calls(log: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_full_run_commands_env_and_cleanup(
    fake_tf: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary, log = fake_tf
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "abc")
    monkeypatch.setenv("TF_VAR_region", "eu")
    monkeypatch.setenv("TF_LOG", "DEBUG")
    plan = get_plan(
        working_dir=str(tmp_path),
        tf_path=str(binary),
        workspace_name="dev",
        tf_vars_files=["a.tfvars", ""],
        tf_vars=["x=1", "y=2"],
        tf_backend_configs=["b.tfbackend"],
    )
    assert plan.format_version
    seen = calls(log)
    assert [c["args"][0] for c in seen] == ["init", "workspace", "plan", "show"]
    init_args = seen[0]["args"]
    assert "-upgrade=true" in init_args and "-backend-config=b.tfbackend" in init_args
    assert seen[1]["args"] == ["workspace", "select", "-no-color", "dev"]
    plan_args = seen[2]["args"]
    assert "-var-file=a.tfvars" in plan_args and "-detailed-exitcode" in plan_args
    assert plan_args[plan_args.index("-var") + 1] == "x=1"
    env = seen[0]["env"]
    assert env["AWS_ACCESS_KEY_ID"] == "abc" and env["TF_VAR_region"] == "eu"
    assert env["TF_LOG"] == "" and env["TF_IN_AUTOMATION"] == "1"
    assert seen[0]["cwd"] == str(tmp_path.resolve())
    out = next(a for a in plan_args if str(a).startswith("-out="))[5:]
    assert not os.path.exists(out) and not os.path.exists(os.path.dirname(out))  # temp dir removed


def test_failure_carries_stderr(fake_tf: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary, _ = fake_tf
    monkeypatch.setenv("FAKE_TF_FAIL", "plan")
    with pytest.raises(TerraformError, match=r"Unable to run Plan: exit status 1[\s\S]*boom from plan"):
        get_plan(working_dir=str(tmp_path), tf_path=str(binary))


def test_plan_json_path_skips_terraform(fake_tf: tuple[Path, Path], tmp_path: Path) -> None:
    binary, log = fake_tf
    p = tmp_path / "p.json"
    shutil.copy(tmp_path / "plan.json", p)
    get_plan(working_dir=str(tmp_path), tf_path=str(binary), plan_json_path=str(p))
    assert not log.exists()


def test_plan_path_uses_show(fake_tf: tuple[Path, Path], tmp_path: Path) -> None:
    binary, log = fake_tf
    get_plan(working_dir=str(tmp_path), tf_path=str(binary), plan_path="/x/plan.bin")
    assert calls(log)[0]["args"] == ["show", "-json", "-no-color", "/x/plan.bin"]


def test_missing_binary_and_bad_json(tmp_path: Path) -> None:
    with pytest.raises(TerraformError):
        Terraform(tmp_path, tmp_path / "nope")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(PlanError, match="Unable to read Plan"):
        load_plan_json(bad)
    with pytest.raises(PlanError, match="Unable to read Plan"):
        load_plan_json(tmp_path / "missing.json")


@pytest.mark.parametrize("fixture", FIXTURES)
def test_every_fixture_plan_loads(fixture: str, tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(load_js(GOLDEN / fixture / "plan.js")))
    assert load_plan_json(p).to_json() == load_js(GOLDEN / fixture / "plan.js")


@pytest.mark.skipif(not os.environ.get("TERRAFORM_BIN"), reason="set TERRAFORM_BIN to run against real Terraform")
def test_real_terraform_simple_fixture(tmp_path: Path) -> None:
    src = Path(__file__).parent / "fixtures" / "simple-test"
    shutil.copytree(src, tmp_path / "w")
    plan = get_plan(working_dir=str(tmp_path / "w"), tf_path=os.environ["TERRAFORM_BIN"])
    got = plan.to_json()
    want = load_js(GOLDEN / "simple-test" / "plan.js")
    got.pop("terraform_version", None)
    want.pop("terraform_version", None)
    assert got == want
