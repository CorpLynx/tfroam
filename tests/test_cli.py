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

from tfroam import screenshot
from tfroam.cli import _ATTRS, FLAGS, Options, UsageError, main, parse_args, split_list, usage_text

from .golden_util import FIXTURES, GOLDEN, load_js
from .test_terraform import FAKE

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fake_tf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """The fake Terraform from test_terraform: logs every call to calls.jsonl, serves simple-test's plan."""
    if sys.platform == "win32":
        pytest.skip("the fake terraform is a shebang script")
    binary = tmp_path / "terraform"
    binary.write_text(FAKE)
    binary.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    plan = tmp_path / "fake-plan.json"
    plan.write_text(json.dumps(load_js(GOLDEN / "simple-test" / "plan.js")))
    monkeypatch.setenv("FAKE_TF_LOG", str(log))
    monkeypatch.setenv("FAKE_TF_PLAN", str(plan))
    monkeypatch.chdir(tmp_path)
    return binary, log


def _tf_args(log: Path) -> list[list[str]]:
    return [json.loads(line)["args"] for line in log.read_text().splitlines()] if log.exists() else []


def _wd() -> str:
    return str(FIXTURE_DIR / "simple-test")


def test_defaults() -> None:
    o = parse_args([])
    assert (o.tf_path, o.working_dir, o.name, o.zip_file_name) == ("/bin/terraform", ".", "tfroam", "tfroam")
    assert o.ip_port == "127.0.0.1:9000"  # Go: 0.0.0.0:9000; loopback unless asked (Docker passes -ipPort)
    assert o.image_path == "tfroam.svg"
    assert not (o.standalone or o.gen_image or o.show_sensitive or o.version or o.tf_upgrade or o.verbose)
    assert o.tf_lock and o.tf_refresh


def test_flag_table_options_and_attrs_agree() -> None:
    assert set(_ATTRS) == {f.name for f in FLAGS}
    defaults = Options()
    for spec in FLAGS:
        if spec.kind != "list":
            assert getattr(defaults, _ATTRS[spec.name]) == spec.default, spec.name


def test_last_ip_port_wins() -> None:
    # The Docker image passes `-ipPort 0.0.0.0:9000` before the user's own arguments.
    assert parse_args(["-ipPort", "0.0.0.0:9000", "-ipPort=127.0.0.1:8080"]).ip_port == "127.0.0.1:8080"
    assert parse_args(["-ipPort", "127.0.0.1:1", "--ipPort", "0.0.0.0:9000"]).ip_port == "0.0.0.0:9000"
    assert parse_args(["-ipPort", "0.0.0.0:9000", "-standalone"]).given == {"ipPort", "standalone"}


@pytest.mark.parametrize(
    ("argv", "lock", "refresh"),
    [
        ([], True, True),
        (["-tfLock=false"], False, True),
        (["-tfLock", "false"], False, True),
        (["--tfLock=0"], False, True),
        (["-tfLock=F"], False, True),
        (["-tfRefresh", "FALSE"], True, False),
        (["-tfRefresh=f"], True, False),
        (["-tfLock=false", "-tfRefresh", "false"], False, False),
        (["-tfLock"], True, True),  # bare bool flag means true
        (["-tfLock", "false", "-tfLock"], True, True),  # last one wins
        (["-tfLock=true", "-tfRefresh", "true"], True, True),
    ],
)
def test_true_default_bools_can_be_turned_off(argv: list[str], lock: bool, refresh: bool) -> None:
    o = parse_args(argv)
    assert (o.tf_lock, o.tf_refresh) == (lock, refresh)


def test_bool_followed_by_false_keeps_parsing() -> None:
    o = parse_args(["-tfLock", "false", "-tfUpgrade", "-verbose", "true", "-imagePath", "g.svg"])
    assert (o.tf_lock, o.tf_upgrade, o.verbose, o.image_path) == (False, True, True, "g.svg")


def test_usage_text_defaults_are_accurate() -> None:
    text = usage_text()
    lines = text.splitlines()

    def help_for(flag: str) -> str:
        head = next(i for i, line in enumerate(lines) if line.split()[:1] == [f"-{flag}"])
        return lines[head + 1]

    assert '(default "terraform" on PATH, else "/bin/terraform")' in help_for("tfPath")
    assert '(default "127.0.0.1:9000")' in help_for("ipPort")
    assert '(default "tfroam.svg")' in help_for("imagePath")
    assert help_for("tfLock").endswith("(default true)") and help_for("tfRefresh").endswith("(default true)")
    for flag in ("tfUpgrade", "verbose", "genImage", "standalone"):  # false defaults: not printed, as in Go
        assert "(default" not in help_for(flag)
    assert "0.0.0.0" not in text
    assert "  -tfLock\n" in text and "  -imagePath string\n" in text


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
    assert capsys.readouterr().out.startswith("tfroam v")
    assert main(["-h"]) == 0 and "Usage of tfroam" in capsys.readouterr().err
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
        names = set(zf.namelist())
        assert {"index.html", "rso.js", "map.js", "graph.js"} <= names
        assert "plan.js" not in names  # Go wrote it; the port does not (see PORTING_NOTES)


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
        "Starting tfroam",
        "Using provided JSON plan",
        "Generating resource overview",
        "Generating resource map",
        "Generating resource graph",
        "Done generating assets",
        f"tfroam is running on 127.0.0.1:{port}",
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


def test_fake_terraform_is_really_used(fake_tf: tuple[Path, Path]) -> None:
    """Control for the 'never invoked' tests below: a valid run does call the fake."""
    binary, log = fake_tf
    assert main(["-tfPath", str(binary), "-workingDir", _wd(), "-standalone"]) == 0
    assert [a[0] for a in _tf_args(log)] == ["init", "plan", "show"]


def test_invalid_hide_fails_before_terraform_runs(
    fake_tf: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    binary, log = fake_tf
    assert main(["-tfPath", str(binary), "-workingDir", _wd(), "-hide", "locals,nope", "-standalone"]) == 1
    err = capsys.readouterr().err
    assert "unknown -hide value 'nope'" in err and "Initializing Terraform" not in err
    assert _tf_args(log) == []


def test_missing_playwright_fails_before_terraform_runs(
    fake_tf: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    binary, log = fake_tf
    monkeypatch.setattr(screenshot, "playwright_available", lambda: False)
    assert main(["-tfPath", str(binary), "-workingDir", _wd(), "-genImage"]) == 1
    err = capsys.readouterr().err
    assert "pipx inject tfroam playwright" in err and "Initializing Terraform" not in err
    assert _tf_args(log) == []


def test_standalone_with_gen_image_does_not_need_playwright(
    fake_tf: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    binary, _ = fake_tf
    monkeypatch.setattr(screenshot, "playwright_available", lambda: False)
    # -standalone exits before image mode, so (as before) Playwright is not required.
    assert main(["-tfPath", str(binary), "-workingDir", _wd(), "-standalone", "-genImage"]) == 0


@pytest.mark.parametrize("flag", [["-tfcOrg", "o"], ["-tfcWorkspace", "w"], ["-tfcNewRun"]])
def test_terraform_cloud_flags_fail_before_terraform_runs(fake_tf: tuple[Path, Path], flag: list[str]) -> None:
    binary, log = fake_tf
    assert main(["-tfPath", str(binary), "-workingDir", _wd(), *flag]) == 1
    assert _tf_args(log) == []


def test_terraform_flags_reach_init_and_plan(fake_tf: tuple[Path, Path]) -> None:
    binary, log = fake_tf
    assert main(["-tfPath", str(binary), "-workingDir", _wd(), "-standalone"]) == 0
    init, plan = _tf_args(log)[0], _tf_args(log)[1]
    assert "-upgrade=false" in init and "-force-copy" not in init
    assert "-lock=true" in plan and "-refresh=true" in plan

    log.unlink()
    argv = ["-tfPath", str(binary), "-workingDir", _wd(), "-tfUpgrade", "-tfLock=false", "-tfRefresh", "false"]
    assert main([*argv, "-standalone"]) == 0
    init, plan = _tf_args(log)[0], _tf_args(log)[1]
    assert "-upgrade=true" in init
    assert "-lock=false" in plan and "-refresh=false" in plan


def test_verbose_flag_streams_terraform_output(fake_tf: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    binary, _ = fake_tf
    assert main(["-tfPath", str(binary), "-workingDir", _wd(), "-standalone"]) == 0
    assert "fake init progress" not in capsys.readouterr().err  # default: captured
    assert main(["-tfPath", str(binary), "-workingDir", _wd(), "-standalone", "-verbose"]) == 0
    err = capsys.readouterr().err
    assert "fake init progress" in err and "fake plan warning" in err
