from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

from tfroam.cli import UsageError, main, parse_args, split_list

from .golden_util import FIXTURES, GOLDEN, load_js

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_defaults_match_go() -> None:
    o = parse_args([])
    assert (o.tf_path, o.working_dir, o.name, o.zip_file_name, o.ip_port) == (
        "/bin/terraform",
        ".",
        "rover",
        "rover",
        "0.0.0.0:9000",
    )
    assert not (o.standalone or o.gen_image or o.show_sensitive or o.version)


def test_go_style_flag_syntax() -> None:
    o = parse_args(
        [
            "-workingDir",
            "x",
            "--tfPath=/t",
            "-planJSONPath=p.json",
            "-standalone",
            "true",
            "-genImage=false",
            "-showSensitive",
        ]
    )
    assert (o.working_dir, o.tf_path, o.plan_json_path) == ("x", "/t", "p.json")
    assert o.standalone and not o.gen_image and o.show_sensitive


def test_repeatable_flags_and_comma_splitting() -> None:
    o = parse_args(["-tfVar", "a=1", "-tfVar", "b=2", "-tfVarsFile", "x.tfvars,y.tfvars", "-tfBackendConfig", "c"])
    assert split_list(o.tf_vars) == ["a=1", "b=2"]
    assert split_list(o.tf_vars_files) == ["x.tfvars", "y.tfvars"]
    assert split_list([]) == []


def test_parsing_stops_at_first_positional_argument() -> None:
    assert parse_args(["stray", "-standalone"]).standalone is False


@pytest.mark.parametrize("argv", [["-nope"], ["-workingDir"], ["-standalone=maybe"]])
def test_bad_flags(argv: list[str]) -> None:
    with pytest.raises(UsageError):
        parse_args(argv)


def test_version_help_and_bad_usage_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-version"]) == 0
    assert capsys.readouterr().out.startswith("Rover v")
    assert main(["-h"]) == 0 and "Usage of rover" in capsys.readouterr().err
    assert main(["-nope"]) == 2 and "flag provided but not defined: -nope" in capsys.readouterr().err


def test_terraform_cloud_flags_fail_clearly(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-tfcWorkspace", "ws"]) == 1
    assert "Terraform Cloud" in capsys.readouterr().err


def test_failed_plan_exits_nonzero_with_message(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-tfPath", str(tmp_path / "missing"), "-workingDir", str(tmp_path)]) == 1
    assert "Unable to parse Plan" in capsys.readouterr().err


@pytest.mark.parametrize("fixture", [f for f in FIXTURES if f != "plan-only-test"])
def test_standalone_end_to_end(fixture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(load_js(GOLDEN / fixture / "plan.js")))
    wd = FIXTURE_DIR / fixture
    wd = wd / "broken" if fixture == "noconfig-test" else wd
    monkeypatch.chdir(tmp_path)
    code = main(["-workingDir", str(wd), "-planJSONPath", "plan.json", "-standalone", "true", "-zipFileName", "site"])
    assert code == 0
    with zipfile.ZipFile(tmp_path / "site.zip") as zf:
        assert {"index.html", "plan.js", "rso.js", "map.js", "graph.js"} <= set(zf.namelist())


def test_serve_end_to_end_in_a_subprocess(tmp_path: Path) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(load_js(GOLDEN / "simple-test" / "plan.js")))
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parent.parent)}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tfroam",
            "-workingDir",
            str(FIXTURE_DIR / "simple-test"),
            "-planJSONPath",
            str(plan),
            "-ipPort",
            f"127.0.0.1:{port}",
        ],
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    try:
        deadline = time.time() + 20
        body = b""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/rso") as resp:
                    body = resp.read()
                break
            except OSError:
                time.sleep(0.2)
        assert json.loads(body) == load_js(GOLDEN / "simple-test" / "rso.js") | {"configs": json.loads(body)["configs"]}
    finally:
        proc.terminate()
        err = proc.communicate(timeout=10)[1]
    # progress lines, in the original's order
    order = [
        "Starting Rover",
        "Using provided JSON plan",
        "Generating resource overview",
        "Generating resource map",
        "Generating resource graph",
        "Done generating assets",
        f"Rover is running on 127.0.0.1:{port}",
    ]
    positions = [err.index(msg) for msg in order]
    assert positions == sorted(positions)


def test_tf_path_defaults_to_path_lookup_but_keeps_go_default_as_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tfroam.cli import resolve_tf_path

    fake = tmp_path / ("terraform.exe" if sys.platform == "win32" else "terraform")
    fake.write_text("")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    def same(found: str, expected: Path) -> bool:  # Windows may report terraform.EXE for terraform.exe
        return os.path.normcase(found) == os.path.normcase(str(expected))

    assert same(resolve_tf_path("/bin/terraform", explicit=False), fake)  # not given: look on PATH
    assert resolve_tf_path("/bin/terraform", explicit=True) == "/bin/terraform"  # explicit: as given
    assert same(resolve_tf_path("terraform", explicit=True), fake)  # bare name: looked up
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert resolve_tf_path("/bin/terraform", explicit=False) == "/bin/terraform"  # Go default fallback
    assert parse_args(["-tfPath", "x"]).given == {"tfPath"}


def test_json_paths_use_forward_slashes_on_windows() -> None:
    from tfroam.config import to_posix

    assert to_posix("C:\\work\\.\\mod", sep="\\") == "C:/work/./mod"
    assert to_posix("a/b", sep="/") == "a/b"


def test_hide_flag_reaches_the_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json as _json

    plan = tmp_path / "plan.json"
    plan.write_text(_json.dumps(load_js(GOLDEN / "random-test" / "plan.js")))
    monkeypatch.chdir(FIXTURE_DIR / "random-test")
    base = ["-workingDir", ".", "-planJSONPath", str(plan), "-standalone", "true"]

    def graph_types(zip_name: str, *extra: str) -> set[str]:
        assert main([*base, "-zipFileName", str(tmp_path / zip_name), *extra]) == 0
        with zipfile.ZipFile(tmp_path / f"{zip_name}.zip") as zf:
            graph = _json.loads(zf.read("graph.js").decode().split("=", 1)[1])
        return {n["data"].get("type", "") for n in graph["nodes"]}

    assert {"variable", "output", "locals"} <= graph_types("full")  # default: unchanged
    assert graph_types("simple", "-hide", "locals,variables") & {"variable", "locals"} == set()
    assert "output" in graph_types("kept-outputs", "-hide", "locals", "-hide", "variables")  # repeatable too


def test_hide_with_a_bad_value_fails_clearly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(load_js(GOLDEN / "simple-test" / "plan.js")))
    code = main(["-workingDir", str(FIXTURE_DIR / "simple-test"), "-planJSONPath", str(plan), "-hide", "nope"])
    assert code == 1
    assert "unknown -hide value 'nope'" in capsys.readouterr().err
