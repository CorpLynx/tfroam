from __future__ import annotations

import io
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from tfroam.plan import PlanError
from tfroam.terraform import Terraform, TerraformError, get_plan, load_plan_json

from .golden_util import FIXTURES, GOLDEN, load_js

FAKE = """#!/usr/bin/env python3
import json, os, sys
log = os.environ["FAKE_TF_LOG"]
with open(log, "a") as fh:
    fh.write(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd(),
        "env": {k: v for k, v in os.environ.items() if k.startswith(("TF_", "AWS_"))}}) + "\\n")
cmd = sys.argv[1]
if cmd != "show":
    print("fake " + cmd + " progress", flush=True)
    sys.stderr.write("fake " + cmd + " warning\\n"); sys.stderr.flush()
wait_for = os.environ.get("FAKE_TF_WAIT_FOR")
if wait_for and cmd == "init":  # only finishes once the caller has seen the progress line
    import time
    deadline = time.time() + 10
    while not os.path.exists(wait_for):
        if time.time() > deadline:
            sys.stderr.write("output was not streamed live\\n"); sys.exit(3)
        time.sleep(0.05)
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
    if sys.platform == "win32":
        pytest.skip("the fake terraform is a shebang script")
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
    # Defaults: no -force-copy (never migrate state silently), -upgrade=false (keep the lock file).
    assert seen[0]["args"] == [
        "init",
        "-no-color",
        "-input=false",
        "-backend=true",
        "-get=true",
        "-upgrade=false",
        "-backend-config=b.tfbackend",
    ]
    assert seen[1]["args"] == ["workspace", "select", "-no-color", "dev"]
    plan_args = seen[2]["args"]
    out_arg = next(a for a in plan_args if str(a).startswith("-out="))
    assert plan_args == [
        "plan",
        "-no-color",
        "-input=false",
        "-detailed-exitcode",
        out_arg,
        "-var-file=a.tfvars",
        "-lock=true",
        "-parallelism=10",
        "-refresh=true",
        "-var",
        "x=1",
        "-var",
        "y=2",
    ]
    assert seen[3]["args"][:3] == ["show", "-json", "-no-color"]
    env = seen[0]["env"]
    assert env["AWS_ACCESS_KEY_ID"] == "abc" and env["TF_VAR_region"] == "eu"
    assert env["TF_LOG"] == "" and env["TF_IN_AUTOMATION"] == "1"
    assert seen[0]["cwd"] == str(tmp_path.resolve())
    out = next(a for a in plan_args if str(a).startswith("-out="))[5:]
    assert not os.path.exists(out) and not os.path.exists(os.path.dirname(out))  # temp dir removed


def test_upgrade_lock_and_refresh_options(fake_tf: tuple[Path, Path], tmp_path: Path) -> None:
    binary, log = fake_tf
    get_plan(working_dir=str(tmp_path), tf_path=str(binary), tf_upgrade=True, tf_lock=False, tf_refresh=False)
    init_args, plan_args = calls(log)[0]["args"], calls(log)[1]["args"]
    assert "-upgrade=true" in init_args and "-upgrade=false" not in init_args
    assert "-force-copy" not in init_args
    assert "-lock=false" in plan_args and "-refresh=false" in plan_args
    assert "-lock=true" not in plan_args and "-refresh=true" not in plan_args


def test_quiet_by_default(fake_tf: tuple[Path, Path], tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    binary, _ = fake_tf
    get_plan(working_dir=str(tmp_path), tf_path=str(binary))
    out, err = capfd.readouterr()
    assert "fake" not in out and "fake" not in err  # captured, not shown


def test_verbose_streams_init_workspace_and_plan_but_not_show(fake_tf: tuple[Path, Path], tmp_path: Path) -> None:
    binary, _ = fake_tf
    stream = io.StringIO()
    tf = Terraform(tmp_path, binary, verbose=True, stream=stream)
    tf.init()
    tf.workspace_select("dev")
    out = str(tmp_path / "plan.bin")
    tf.plan(out)
    plan = tf.show_plan_file(out)
    assert plan.format_version  # show -json stdout still captured and parsed
    lines = stream.getvalue().splitlines()
    for cmd in ("init", "workspace", "plan"):
        assert f"fake {cmd} progress" in lines and f"fake {cmd} warning" in lines
    assert not any("format_version" in line for line in lines)  # show output never streamed


def test_verbose_writes_to_stderr_through_get_plan(
    fake_tf: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    binary, _ = fake_tf
    get_plan(working_dir=str(tmp_path), tf_path=str(binary), verbose=True)
    out, err = capsys.readouterr()
    assert "fake init progress" in err and "fake plan warning" in err
    assert "fake" not in out and "format_version" not in err


def test_verbose_output_is_live(fake_tf: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake init blocks until the progress line has reached the stream, so buffering would fail it."""
    binary, _ = fake_tf
    go = tmp_path / "seen"
    monkeypatch.setenv("FAKE_TF_WAIT_FOR", str(go))

    class Signalling(io.StringIO):
        def write(self, s: str) -> int:
            if "fake init progress" in s:
                go.touch()
            return super().write(s)

    Terraform(tmp_path, binary, verbose=True, stream=Signalling()).init()


def test_verbose_failure_still_carries_stderr(
    fake_tf: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary, _ = fake_tf
    monkeypatch.setenv("FAKE_TF_FAIL", "init")
    stream = io.StringIO()
    with pytest.raises(TerraformError, match=r"exit status 1[\s\S]*boom from init") as info:
        Terraform(tmp_path, binary, verbose=True, stream=stream).init()
    assert "fake init warning" in str(info.value)
    assert "boom from init" in stream.getvalue()  # also shown live
    with pytest.raises(TerraformError, match="unable to run"):
        Terraform(tmp_path, tmp_path / "nope", verbose=True, stream=stream).init()


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
    tf = Terraform(tmp_path, tmp_path / "nope")  # binary is only needed once a command runs
    with pytest.raises(TerraformError, match="unable to run"):
        tf.init()
    with pytest.raises(TerraformError, match="error initialising Terraform with workdir"):
        Terraform(tmp_path / "missing-dir", "terraform")
    with pytest.raises(TerraformError, match="please supply the path"):
        Terraform(tmp_path, "")
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
    for doc in (got, want):  # the goldens were captured with Terraform 1.9.8; the version is not content
        doc.pop("terraform_version", None)
        doc.get("prior_state", {}).pop("terraform_version", None)
    assert got == want


@pytest.mark.skipif(not os.environ.get("TERRAFORM_BIN"), reason="set TERRAFORM_BIN to run against real Terraform")
def test_real_terraform_refuses_backend_change_instead_of_migrating(tmp_path: Path) -> None:
    """Without -force-copy, a changed backend makes init fail with Terraform's message; no state is copied."""
    tf_bin = os.environ["TERRAFORM_BIN"]
    wd = tmp_path / "w"
    wd.mkdir()
    config = 'terraform {{\n  backend "local" {{\n    path = "{}"\n  }}\n}}\noutput "x" {{\n  value = 1\n}}\n'
    (wd / "main.tf").write_text(config.format("a.tfstate"))
    tf = Terraform(wd, tf_bin)
    tf.init()
    tf._run("apply", "-auto-approve", "-input=false", "-no-color")
    assert (wd / "a.tfstate").exists()
    (wd / "main.tf").write_text(config.format("b.tfstate"))
    with pytest.raises(TerraformError, match="Backend configuration changed"):
        get_plan(working_dir=str(wd), tf_path=tf_bin)
    assert not (wd / "b.tfstate").exists()
