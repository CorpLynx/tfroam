"""Opening plans automatically: config resolution, the shell hook and ``tfroam plan`` (tfroam/auto.py)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tfroam import auto, cli
from tfroam.auto import ConfigError, TfCommand

from .golden_util import GOLDEN, load_js

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def home(tmp_path: Path) -> dict[str, str]:
    """An environment whose global config lives under tmp_path (never the real ~/.config)."""
    return {"TFROAM_CONFIG": str(tmp_path / "global.toml")}


def global_cfg(env: dict[str, str], text: str) -> None:
    write(Path(env["TFROAM_CONFIG"]), text)


# ---- deciding whether a root wants tfroam --------------------------------------------------------


def test_off_without_any_config(tmp_path: Path, home: dict[str, str]) -> None:
    s = auto.resolve(tmp_path, home)
    assert not s.enabled and "no config" in s.reason
    assert s.view == auto.View()


def test_global_enabled_everywhere(tmp_path: Path, home: dict[str, str]) -> None:
    global_cfg(home, "[auto]\nenabled = true\n")
    s = auto.resolve(tmp_path, home)
    assert s.enabled and str(home["TFROAM_CONFIG"]) in s.reason


def test_global_roots_and_exclude(tmp_path: Path, home: dict[str, str]) -> None:
    infra, other, scratch = tmp_path / "infra" / "prod", tmp_path / "other", tmp_path / "infra" / "scratch" / "x"
    for d in (infra, other, scratch):
        d.mkdir(parents=True)
    global_cfg(
        home,
        f'[auto]\nenabled = true\nroots = ["{tmp_path / "infra"}"]\nexclude = ["{tmp_path}/infra/scratch*"]\n',
    )
    assert auto.resolve(infra, home).enabled
    assert auto.resolve(tmp_path / "infra", home).enabled  # the root itself, not only what is under it
    off = auto.resolve(other, home)
    assert not off.enabled and "not under any [auto] roots" in off.reason
    excluded = auto.resolve(scratch, home)
    assert not excluded.enabled and "excluded by" in excluded.reason


def test_roots_expand_home(tmp_path: Path, home: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "infra" / "a").mkdir(parents=True)
    global_cfg(home, '[auto]\nenabled = true\nroots = ["~/infra"]\n')
    assert auto.resolve(tmp_path / "infra" / "a", home).enabled


def test_relative_root_is_an_error(tmp_path: Path, home: dict[str, str]) -> None:
    global_cfg(home, '[auto]\nenabled = true\nroots = ["infra"]\n')
    with pytest.raises(ConfigError, match="absolute path"):
        auto.resolve(tmp_path, home)


def test_nearest_local_config_wins_over_global(tmp_path: Path, home: dict[str, str]) -> None:
    global_cfg(home, "[auto]\nenabled = true\n")
    write(tmp_path / "repo" / ".tfroam.toml", "[auto]\nenabled = false\n")
    write(tmp_path / "repo" / "keep" / ".tfroam.toml", "[auto]\nenabled = true\n")
    (tmp_path / "repo" / "legacy" / "deep").mkdir(parents=True)
    (tmp_path / "repo" / "keep" / "deep").mkdir(parents=True)

    off = auto.resolve(tmp_path / "repo" / "legacy" / "deep", home)
    assert not off.enabled and off.local_path == tmp_path / "repo" / ".tfroam.toml"
    on = auto.resolve(tmp_path / "repo" / "keep" / "deep", home)
    assert on.enabled and on.local_path == tmp_path / "repo" / "keep" / ".tfroam.toml"


def test_local_view_only_falls_back_to_global_auto(tmp_path: Path, home: dict[str, str]) -> None:
    global_cfg(home, '[auto]\nenabled = true\n[view]\nmode = "page"\nhide = ["locals"]\n')
    write(tmp_path / ".tfroam.toml", '[view]\nhide = ["locals", "variables"]\nopen_browser = false\n')
    s = auto.resolve(tmp_path, home)
    assert s.enabled  # the local file says nothing about [auto]
    assert (s.view.mode, s.view.hide, s.view.open_browser) == ("page", ["locals", "variables"], False)


@pytest.mark.parametrize(("value", "want"), [("0", False), ("off", False), ("1", True), ("yes", True)])
def test_env_overrides_everything(tmp_path: Path, home: dict[str, str], value: str, want: bool) -> None:
    global_cfg(home, "[auto]\nenabled = true\n" if not want else "[auto]\nenabled = false\n")
    s = auto.resolve(tmp_path, {**home, "TFROAM_AUTO": value})
    assert s.enabled is want and "TFROAM_AUTO" in s.reason


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("[auto]\nenabld = true\n", "unknown key 'enabld'"),
        ("[autos]\nenabled = true\n", "unknown section"),
        ('[auto]\nenabled = "yes"\n', "must be bool"),
        ('[view]\nhide = "locals"\n', "must be a list of strings"),
        ('[view]\nmode = "tv"\n', "mode must be one of"),
        ('[view]\nhide = ["resources"]\n', "hide"),
        ("[auto\n", "global.toml"),
    ],
)
def test_bad_config_is_an_error(tmp_path: Path, home: dict[str, str], text: str, error: str) -> None:
    global_cfg(home, text)
    with pytest.raises(ConfigError, match=error):
        auto.resolve(tmp_path, home)


def test_roots_only_in_the_global_config(tmp_path: Path, home: dict[str, str]) -> None:
    write(tmp_path / ".tfroam.toml", '[auto]\nroots = ["/x"]\n')
    with pytest.raises(ConfigError, match="belongs in the global config"):
        auto.resolve(tmp_path, home)


def test_global_path_follows_xdg(tmp_path: Path) -> None:
    assert auto.global_config_path({"XDG_CONFIG_HOME": str(tmp_path)}) == tmp_path / "tfroam" / "config.toml"
    assert auto.global_config_path({"TFROAM_CONFIG": str(tmp_path / "c.toml")}) == tmp_path / "c.toml"


# ---- reading the terraform command line ------------------------------------------------------------


def test_split_command() -> None:
    c = auto.split_command(["-chdir=envs/prod", "plan", "-var", "a=b"])
    assert (c.global_args, c.subcommand, c.args) == (["-chdir=envs/prod"], "plan", ["-var", "a=b"])
    assert c.chdir == "envs/prod"
    assert auto.split_command(["-version"]).subcommand is None
    # "plan" as an argument is not the command
    assert auto.split_command(["state", "show", "plan"]).subcommand == "state"


def test_out_path_and_plain_terraform() -> None:
    assert auto.out_path(["-out=p.tfplan"]) == "p.tfplan"
    assert auto.out_path(["-var", "x=1", "-out", "p.tfplan"]) == "p.tfplan"
    assert auto.out_path(["-var", "out=1"]) is None
    assert auto.wants_plain_terraform(["-json"]) and auto.wants_plain_terraform(["-help"])
    assert not auto.wants_plain_terraform(["-var", "json=1", "-detailed-exitcode"])


# ---- the shell hook ---------------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_shell_init_is_valid_shell(shell: str) -> None:
    exe = shutil.which(shell)
    if exe is None:
        pytest.skip(f"{shell} not installed")
    script = auto.shell_init(shell)
    assert "command tfroam hook terraform" in script and "command terraform" in script
    subprocess.run([exe, "-n"], input=script, text=True, check=True)


def test_shell_init_fish_and_unknown() -> None:
    assert "function terraform --wraps terraform" in auto.shell_init("fish")
    with pytest.raises(ConfigError, match="unsupported shell"):
        auto.shell_init("tcsh")


def test_bash_hook_routes_only_plan(tmp_path: Path) -> None:
    """The function itself: plan goes to `tfroam hook`, everything else straight to terraform."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not installed")
    bin_dir = tmp_path / "bin"
    for name in ("terraform", "tfroam"):
        script = write(bin_dir / name, f'#!/bin/sh\necho "{name} $*"\n')
        script.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    run = auto.shell_init("bash") + "\nterraform plan -var a=b\nterraform apply\nterraform -chdir=x plan\n"
    out = subprocess.run([bash, "-c", run], capture_output=True, text=True, env=env, check=True).stdout.splitlines()
    assert out == ["tfroam hook terraform plan -var a=b", "terraform apply", "tfroam hook terraform -chdir=x plan"]


class Exec(Exception):
    pass


@pytest.fixture
def execs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake_exec(file: str, args: list[str]) -> None:
        seen.append(args)
        raise Exec

    monkeypatch.setattr(os, "execvp", fake_exec)
    return seen


def test_hook_passes_other_commands_straight_through(execs: list[list[str]], home: dict[str, str]) -> None:
    global_cfg(home, "[auto]\nenabled = true\n")
    with pytest.raises(Exec):
        auto.main(["hook", "terraform", "apply", "-auto-approve"], {**home, "TFROAM_AUTO": "1"})
    assert execs == [["terraform", "apply", "-auto-approve"]]


@pytest.mark.parametrize(
    ("env_extra", "args"),
    [
        ({}, ["plan"]),  # auto is off (no config enables it)
        ({"TFROAM_AUTO": "1"}, ["plan", "-json"]),  # machine output
        ({"TFROAM_AUTO": "1"}, ["plan", "-help"]),
    ],
)
def test_hook_leaves_plan_alone(
    execs: list[list[str]], home: dict[str, str], tmp_path: Path, env_extra: dict[str, str], args: list[str]
) -> None:
    with pytest.raises(Exec):
        auto.main(["hook", "terraform", f"-chdir={tmp_path}", *args], {**home, **env_extra})
    assert execs == [["terraform", f"-chdir={tmp_path}", *args]]


def test_hook_skips_non_interactive_runs(
    execs: list[list[str]], home: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_cfg(home, "[auto]\nenabled = true\n")
    monkeypatch.setattr(auto, "interactive", lambda env=None: False)  # CI, a pipe, TF_IN_AUTOMATION
    with pytest.raises(Exec):
        auto.main(["hook", "terraform", f"-chdir={tmp_path}", "plan"], home)
    assert execs == [["terraform", f"-chdir={tmp_path}", "plan"]]


def test_hook_with_a_broken_config_still_runs_terraform(
    execs: list[list[str]], home: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    global_cfg(home, "[auto\n")
    with pytest.raises(Exec):
        auto.main(["hook", "terraform", f"-chdir={tmp_path}", "plan"], home)
    assert execs and "running plain terraform" in capsys.readouterr().err


def test_hook_runs_plan_and_view_when_enabled(
    home: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_cfg(home, "[auto]\nenabled = true\n")
    seen: dict[str, Any] = {}

    def fake(binary: str, command: TfCommand, settings: auto.Settings) -> int:
        seen.update(binary=binary, command=command, root=settings.root)
        return 2

    monkeypatch.setattr(auto, "plan_and_view", fake)
    monkeypatch.setattr(auto, "interactive", lambda env=None: True)
    assert auto.main(["hook", "tofu", f"-chdir={tmp_path}", "plan", "-detailed-exitcode"], home) == 2
    assert seen["binary"] == "tofu" and seen["root"] == tmp_path.resolve()
    assert seen["command"].args == ["-detailed-exitcode"]


# ---- plan, then view (with a stand-in terraform) -------------------------------------------------


def fake_terraform(tmp_path: Path, plan_code: int = 0) -> Path:
    """A `terraform` that records its arguments, writes the -out file and answers `show -json`."""
    plan_json = tmp_path / "plan.json"
    plan_json.write_text(json.dumps(load_js(GOLDEN / "simple-test" / "plan.js")))
    log = tmp_path / "calls.log"
    script = tmp_path / "bin" / "terraform"
    write(
        script,
        f"""#!{sys.executable}
import os, sys
args = sys.argv[1:]
with open({str(log)!r}, "a") as fh:
    fh.write(" ".join(args) + "\\n")
for a in args:
    if a.startswith("-chdir="):
        os.chdir(a.split("=", 1)[1])  # as Terraform does: later relative paths are inside this directory
if "show" in args:
    sys.stdout.write(open({str(plan_json)!r}).read())
    sys.exit(0)
if "plan" in args:
    out = next((a.split("=", 1)[1] for a in args if a.startswith("-out=")), None)
    if out and {plan_code} in (0, 2):
        open(out, "w").write("binary plan")
    print("Plan: 1 to add, 0 to change, 0 to destroy.")
    sys.exit({plan_code})
""",
    )
    script.chmod(0o755)
    return script


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "root"
    shutil.copytree(FIXTURES_DIR / "simple-test", r)
    return r


def page_settings(root: Path, home: dict[str, str]) -> auto.Settings:
    write(root / ".tfroam.toml", '[auto]\nenabled = true\n[view]\nmode = "page"\nopen_browser = false\n')
    return auto.resolve(root, home)


def test_page_mode_writes_a_private_page_and_keeps_terraforms_exit_code(
    tmp_path: Path, root: Path, home: dict[str, str]
) -> None:
    tf = fake_terraform(tmp_path, plan_code=2)
    command = auto.split_command([f"-chdir={root}", "plan", "-detailed-exitcode"])
    assert auto.plan_and_view(str(tf), command, page_settings(root, home)) == 2

    plan_file = root / ".terraform" / "tfroam" / "last.tfplan"
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert calls[0] == f"-chdir={root} plan -detailed-exitcode -out={plan_file}"
    assert calls[1].startswith("show -json") and calls[1].endswith(str(plan_file))
    assert stat.S_IMODE(plan_file.stat().st_mode) == 0o600

    page = root / ".terraform" / "tfroam" / "page"
    assert stat.S_IMODE(page.stat().st_mode) == 0o700
    assert {"index.html", "graph.js", "map.js", "rso.js"} <= {p.name for p in page.iterdir()}
    assert stat.S_IMODE((page / "graph.js").stat().st_mode) == 0o600
    assert "./graph.js" in (page / "index.html").read_text()


def test_a_failed_plan_is_not_viewed(tmp_path: Path, root: Path, home: dict[str, str]) -> None:
    tf = fake_terraform(tmp_path, plan_code=1)
    command = auto.split_command([f"-chdir={root}", "plan"])
    assert auto.plan_and_view(str(tf), command, page_settings(root, home)) == 1
    assert len((tmp_path / "calls.log").read_text().splitlines()) == 1  # no `show`
    assert not (root / ".terraform" / "tfroam" / "page").exists()


def test_the_users_own_out_file_is_used(tmp_path: Path, root: Path, home: dict[str, str]) -> None:
    tf = fake_terraform(tmp_path)
    command = auto.split_command([f"-chdir={root}", "plan", "-out=mine.tfplan"])
    assert auto.plan_and_view(str(tf), command, page_settings(root, home)) == 0
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert calls[0] == f"-chdir={root} plan -out=mine.tfplan"  # nothing added
    assert calls[1].endswith(str(root / "mine.tfplan"))
    assert not (root / ".terraform" / "tfroam" / "last.tfplan").exists()


def test_page_mode_opens_the_browser_when_asked(
    tmp_path: Path, root: Path, home: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)
    write(root / ".tfroam.toml", '[view]\nmode = "page"\n')
    tf = fake_terraform(tmp_path)
    auto.plan_and_view(str(tf), auto.split_command([f"-chdir={root}", "plan"]), auto.resolve(root, home))
    assert opened == [(root / ".terraform" / "tfroam" / "page" / "index.html").resolve().as_uri()]


def test_serve_mode_hands_the_saved_plan_to_the_server(
    tmp_path: Path, root: Path, home: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[cli.Options] = []

    def fake_run(opts: cli.Options) -> int:
        seen.append(opts)
        return 0

    monkeypatch.setattr(cli, "run", fake_run)
    global_cfg(home, '[view]\nhide = ["locals"]\n')
    tf = fake_terraform(tmp_path)
    settings = auto.resolve(root, home)
    assert auto.plan_and_view(str(tf), auto.split_command([f"-chdir={root}", "plan"]), settings) == 0
    (opts,) = seen
    assert opts.plan_path == str(root.resolve() / ".terraform" / "tfroam" / "last.tfplan")
    assert (opts.tf_path, opts.working_dir, opts.open, opts.hide) == (str(tf), str(root.resolve()), True, ["locals"])
    assert opts.ip_port == "127.0.0.1:0" and {"tfPath", "ipPort"} <= opts.given


def test_tfroam_plan_command(tmp_path: Path, root: Path, home: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """`tfroam plan` always views (it was asked for explicitly), with TFROAM_TF choosing the binary."""
    tf = fake_terraform(tmp_path)
    write(root / ".tfroam.toml", '[view]\nmode = "page"\nopen_browser = false\n')
    monkeypatch.chdir(root)
    code = auto.main(["plan", "-var", "x=1"], {**home, "TFROAM_TF": str(tf)})
    assert code == 0 and (root / ".terraform" / "tfroam" / "page" / "index.html").is_file()


# ---- `tfroam config` and the CLI entry ---------------------------------------------------------


def test_config_shows_the_decision(tmp_path: Path, home: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    global_cfg(home, '[auto]\nenabled = true\n[view]\nhide = ["locals"]\n')
    assert auto.main(["config", str(tmp_path)], home) == 0
    out = capsys.readouterr().out
    assert "Auto:          on" in out and "hide=locals" in out and "(none: no .tfroam.toml" in out


def test_config_init_writes_templates_once(
    tmp_path: Path, home: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert auto.main(["config", "init"], home) == 0
    assert auto.main(["config", "init", "--global"], home) == 0
    assert auto.load_config(tmp_path / ".tfroam.toml", is_global=False)["auto"]["enabled"] is True
    assert auto.load_config(Path(home["TFROAM_CONFIG"]), is_global=True)["view"]["mode"] == "serve"
    assert auto.main(["config", "init"], home) == 1
    assert "already exists" in capsys.readouterr().err


def test_cli_dispatches_commands_and_documents_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        seen.append(list(argv))
        return 0

    monkeypatch.setattr(auto, "main", fake_main)
    assert cli.main(["shell-init", "zsh"]) == 0 and seen == [["shell-init", "zsh"]]
    assert "tfroam shell-init zsh|bash|fish" in cli.usage_text()


@pytest.mark.parametrize(
    ("ip_port", "url"),
    [
        ("127.0.0.1:0", "http://127.0.0.1:4321/"),
        ("0.0.0.0:9000", "http://127.0.0.1:4321/"),
        ("[::1]:0", "http://[::1]:4321/"),
    ],
)
def test_open_browser_url(monkeypatch: pytest.MonkeyPatch, ip_port: str, url: str) -> None:
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)
    cli.open_browser(ip_port, 4321)
    assert opened == [url]
