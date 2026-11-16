"""Open plans in tfroam automatically: config files, the shell hook and ``tfroam plan``.

Terraform has no post-plan hook, so ``tfroam shell-init`` prints a small shell function that wraps
``terraform`` (and ``tofu``). Every command except ``plan`` goes straight to the real binary. For
``plan``, ``tfroam hook`` reads the config: if this root wants tfroam, it runs the plan with the
terminal attached, saves it, and opens it; otherwise it hands the command to Terraform unchanged.

Config (TOML, both optional):

- global: ``$TFROAM_CONFIG``, else ``$XDG_CONFIG_HOME/tfroam/config.toml``, else
  ``~/.config/tfroam/config.toml``. ``[auto] enabled``, ``roots``, ``exclude``; ``[view]``.
- local: the nearest ``.tfroam.toml`` in the Terraform root or a parent. ``[auto] enabled``; ``[view]``.

``TFROAM_AUTO=0``/``1`` turns it off or on for one command. ``tfroam config`` explains the decision.
"""

from __future__ import annotations

import fnmatch
import os
import signal
import subprocess
import sys
import tomllib
import webbrowser
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .assets import TfroamError

SUBCOMMANDS = ("plan", "hook", "shell-init", "config")

LOCAL_NAME = ".tfroam.toml"
CONFIG_ENV = "TFROAM_CONFIG"
AUTO_ENV = "TFROAM_AUTO"
TF_ENV = "TFROAM_TF"
STATE_DIR = Path(".terraform") / "tfroam"  # inside the root; Terraform's own directory, normally git-ignored
PLAN_FILE = "last.tfplan"
PAGE_DIR = "page"

_TRUE = ("1", "true", "on", "yes")
_FALSE = ("0", "false", "off", "no")
MODES = ("serve", "page")


class ConfigError(TfroamError):
    pass


# ---- config ----------------------------------------------------------------------------------------


@dataclass
class View:
    """How the plan is shown. Defaults suit an interactive shell."""

    mode: str = "serve"  # "serve": live page until Ctrl-C; "page": static page on disk, prompt back at once
    open_browser: bool = True
    ip_port: str = "127.0.0.1:0"  # any free port: plans in several terminals don't clash
    hide: list[str] = field(default_factory=list)
    show_sensitive: bool = False


@dataclass
class Settings:
    root: Path
    global_path: Path
    local_path: Path | None
    enabled: bool
    reason: str
    view: View


_SCHEMA: dict[str, dict[str, type]] = {
    "auto": {"enabled": bool, "roots": list, "exclude": list},
    "view": {"mode": str, "open_browser": bool, "ip_port": str, "hide": list, "show_sensitive": bool},
}
_GLOBAL_ONLY = {("auto", "roots"), ("auto", "exclude")}


def global_config_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get(CONFIG_ENV):
        return Path(env[CONFIG_ENV]).expanduser()
    base = env.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "tfroam" / "config.toml"


def find_local_config(root: Path) -> Path | None:
    """The nearest ``.tfroam.toml`` in ``root`` or one of its parents."""
    for directory in (root, *root.parents):
        candidate = directory / LOCAL_NAME
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path, *, is_global: bool) -> dict[str, dict[str, Any]]:
    """Read and check one config file. Unknown sections, keys and wrong types are errors (they are typos)."""
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: {exc.strerror or exc}") from exc
    out: dict[str, dict[str, Any]] = {}
    for section, values in data.items():
        if section not in _SCHEMA or not isinstance(values, dict):
            raise ConfigError(f"{path}: unknown section [{section}] (expected [auto] or [view])")
        for key, value in values.items():
            want = _SCHEMA[section].get(key)
            if want is None:
                raise ConfigError(f"{path}: unknown key {key!r} in [{section}]")
            if (section, key) in _GLOBAL_ONLY and not is_global:
                raise ConfigError(f"{path}: [{section}] {key} belongs in the global config ({global_config_path()})")
            if not isinstance(value, want) or (isinstance(value, list) and not all(isinstance(v, str) for v in value)):
                kind = "a list of strings" if want is list else want.__name__
                raise ConfigError(f"{path}: [{section}] {key} must be {kind}")
        out[section] = dict(values)
    view = out.get("view", {})
    if "mode" in view and view["mode"] not in MODES:
        raise ConfigError(f"{path}: [view] mode must be one of {', '.join(MODES)}")
    if "hide" in view:
        from .graph import validate_hide

        try:
            validate_hide(view["hide"])
        except ValueError as exc:
            raise ConfigError(f"{path}: [view] hide: {exc}") from exc
    return out


def _matches(path: Path, pattern: str, config: Path) -> bool:
    """``pattern`` is a directory (it and everything under it) or a glob over absolute paths."""
    expanded = os.path.expanduser(pattern)
    if not os.path.isabs(expanded):
        raise ConfigError(f"{config}: {pattern!r} must be an absolute path (or start with ~)")
    if any(ch in expanded for ch in "*?["):
        text = str(path)
        return fnmatch.fnmatchcase(text, expanded) or fnmatch.fnmatchcase(text, expanded.rstrip("/") + "/*")
    base = Path(expanded).resolve()
    return path == base or base in path.parents


def resolve(root: str | Path, env: Mapping[str, str] | None = None) -> Settings:
    """Everything that applies to the Terraform root ``root``, and whether tfroam opens its plans."""
    env = os.environ if env is None else env
    root_path = Path(root).expanduser().resolve()
    gpath = global_config_path(env)
    gcfg = load_config(gpath, is_global=True) if gpath.is_file() else {}
    lpath = find_local_config(root_path)
    lcfg = load_config(lpath, is_global=False) if lpath else {}

    view = View()
    for cfg in (gcfg, lcfg):
        for key, value in cfg.get("view", {}).items():
            setattr(view, key, list(value) if isinstance(value, list) else value)

    forced = env.get(AUTO_ENV, "").strip().lower()
    gauto, lauto = gcfg.get("auto", {}), lcfg.get("auto", {})
    if forced in _FALSE:
        enabled, reason = False, f"{AUTO_ENV}={env[AUTO_ENV]}"
    elif forced in _TRUE:
        enabled, reason = True, f"{AUTO_ENV}={env[AUTO_ENV]}"
    elif lpath and "enabled" in lauto:
        enabled = lauto["enabled"]
        reason = f"[auto] enabled = {str(enabled).lower()} in {lpath}"
    elif gauto.get("enabled"):
        roots, exclude = gauto.get("roots", []), gauto.get("exclude", [])
        hit = [p for p in exclude if _matches(root_path, p, gpath)]
        if roots and not any(_matches(root_path, p, gpath) for p in roots):
            enabled, reason = False, f"not under any [auto] roots in {gpath}"
        elif hit:
            enabled, reason = False, f"excluded by {hit[0]!r} in {gpath}"
        else:
            enabled = True
            reason = f"[auto] enabled = true in {gpath}" + (" (under [auto] roots)" if roots else "")
    else:
        enabled, reason = False, "no config turns [auto] on here"
    return Settings(root_path, gpath, lpath, enabled, reason, view)


# ---- terraform command line --------------------------------------------------------------------


@dataclass
class TfCommand:
    """``terraform [global options] <subcommand> [args]``, split up."""

    global_args: list[str]
    subcommand: str | None
    args: list[str]

    @property
    def chdir(self) -> str | None:
        for arg in self.global_args:
            if arg.startswith("-chdir="):
                return arg.partition("=")[2]
        return None


def split_command(argv: Sequence[str]) -> TfCommand:
    for i, arg in enumerate(argv):
        if not arg.startswith("-"):
            return TfCommand(list(argv[:i]), arg, list(argv[i + 1 :]))
    return TfCommand(list(argv), None, [])


def out_path(plan_args: Sequence[str]) -> str | None:
    """The ``-out`` of a ``terraform plan`` command line, if any (``-out=FILE`` or ``-out FILE``)."""
    for i, arg in enumerate(plan_args):
        name, eq, value = arg.lstrip("-").partition("=")
        if arg.startswith("-") and name == "out":
            if eq:
                return value
            return plan_args[i + 1] if i + 1 < len(plan_args) else None
    return None


def wants_plain_terraform(plan_args: Sequence[str]) -> bool:
    """``-json`` output is for programs and ``-help`` prints usage: leave both to Terraform alone."""
    return any(a.lstrip("-").partition("=")[0] in ("json", "help", "h") for a in plan_args if a.startswith("-"))


def interactive(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return sys.stdin.isatty() and sys.stdout.isatty() and not env.get("TF_IN_AUTOMATION")


def _exec(binary: str, args: Sequence[str]) -> int:
    """Become ``binary args``: no wrapper left between the user and Terraform."""
    try:
        os.execvp(binary, [binary, *args])
    except FileNotFoundError:
        sys.stderr.write(f"tfroam: {binary}: command not found\n")
        return 127
    except OSError as exc:
        sys.stderr.write(f"tfroam: {binary}: {exc.strerror or exc}\n")
        return 126
    return 0  # not reached: execvp only returns by raising


def _run_attached(cmd: Sequence[str]) -> int:
    """Run Terraform on this terminal. Ctrl-C goes to Terraform (it stops cleanly); tfroam keeps waiting."""
    previous = signal.signal(signal.SIGINT, lambda *_: None)  # a handler, not SIG_IGN: SIG_IGN would be inherited
    try:
        return subprocess.call(list(cmd))
    except FileNotFoundError:
        sys.stderr.write(f"tfroam: {cmd[0]}: command not found\n")
        return 127
    finally:
        signal.signal(signal.SIGINT, previous)


# ---- plan, then view ---------------------------------------------------------------------------


def plan_and_view(binary: str, command: TfCommand, settings: Settings) -> int:
    """Run ``terraform plan`` (saving the plan), then open it. Returns Terraform's exit code."""
    root = settings.root
    args = list(command.args)
    given = out_path(args)
    if given:
        plan_file = Path(given) if os.path.isabs(given) else root / given  # Terraform resolves -out in the -chdir dir
    else:
        state = root / STATE_DIR
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        plan_file = state / PLAN_FILE
        args.append(f"-out={plan_file}")

    code = _run_attached([binary, *command.global_args, "plan", *args])
    if code not in (0, 2) or not plan_file.is_file():  # 2: changes, with -detailed-exitcode
        return code
    if not given:
        os.chmod(plan_file, 0o600)  # a saved plan holds every value, sensitive ones included

    try:
        view_plan(binary, plan_file, settings)
    except TfroamError as exc:
        sys.stderr.write(f"tfroam: {exc}\n")
    except KeyboardInterrupt:
        pass
    return code


def view_plan(binary: str, plan_file: Path, settings: Settings) -> None:
    from .cli import Options, run

    view = settings.view
    shown = os.path.relpath(plan_file, Path.cwd()) if plan_file.is_relative_to(Path.cwd()) else str(plan_file)
    if view.mode == "page":
        from .assets import generate_assets
        from .export import write_site

        sys.stderr.write(f"\ntfroam: drawing {shown}\n")
        assets = generate_assets(
            working_dir=str(settings.root),
            tf_path=binary,
            plan_path=str(plan_file),
            show_sensitive=view.show_sensitive,
            hide=view.hide,
        )
        try:
            index = write_site(assets, settings.root / STATE_DIR / PAGE_DIR)
        except OSError as exc:
            raise TfroamError(str(exc)) from exc
        sys.stderr.write(f"tfroam: page written to {index}\n")
        if view.open_browser:
            webbrowser.open(index.as_uri())
        return

    sys.stderr.write(f"\ntfroam: opening {shown} (Ctrl-C to stop tfroam and get your prompt back)\n")
    opts = Options(
        tf_path=binary,
        working_dir=str(settings.root),
        plan_path=str(plan_file),
        ip_port=view.ip_port,
        hide=list(view.hide),
        show_sensitive=view.show_sensitive,
        open=view.open_browser,
    )
    opts.given.update({"tfPath", "ipPort"})
    run(opts)


# ---- shell integration -------------------------------------------------------------------------

_POSIX = """\
# tfroam: open `terraform plan` / `tofu plan` in tfroam where the config says so (`tfroam config`).
# Everything else goes straight to Terraform. Added by: eval "$(tfroam shell-init {shell})"
terraform() {{
  case " $* " in
    *" plan "*) command tfroam hook terraform "$@" ;;
    *) command terraform "$@" ;;
  esac
}}
if command -v tofu >/dev/null 2>&1; then
  tofu() {{
    case " $* " in
      *" plan "*) command tfroam hook tofu "$@" ;;
      *) command tofu "$@" ;;
    esac
  }}
fi
"""

_FISH = """\
# tfroam: open `terraform plan` / `tofu plan` in tfroam where the config says so (`tfroam config`).
# Everything else goes straight to Terraform. Added by: tfroam shell-init fish | source
function terraform --wraps terraform --description 'terraform; plans open in tfroam'
    if contains -- plan $argv
        command tfroam hook terraform $argv
    else
        command terraform $argv
    end
end
if command -q tofu
    function tofu --wraps tofu --description 'tofu; plans open in tfroam'
        if contains -- plan $argv
            command tfroam hook tofu $argv
        else
            command tofu $argv
        end
    end
end
"""

SHELLS = ("zsh", "bash", "fish")


def shell_init(shell: str) -> str:
    if shell == "fish":
        return _FISH
    if shell in ("zsh", "bash"):
        return _POSIX.format(shell=shell)
    raise ConfigError(f"unsupported shell {shell!r}: use one of {', '.join(SHELLS)}")


# ---- `tfroam config` -----------------------------------------------------------------------------

GLOBAL_TEMPLATE = """\
# tfroam settings for every Terraform root. `tfroam config` shows what applies where.

[auto]
# Open the plan in tfroam after `terraform plan`. Needs the shell hook: see `tfroam shell-init`.
enabled = true
# Only in these directories and everything under them (paths or globs; ~ is your home).
# Leave it out for everywhere.
# roots = ["~/infra"]
# Never in these, even under a root.
# exclude = ["~/infra/scratch"]

[view]
# "serve": a live page on 127.0.0.1 until you press Ctrl-C.
# "page": write a static page to .terraform/tfroam/page in the root, open it, and return to the prompt.
mode = "serve"
open_browser = true
# 127.0.0.1:0 picks a free port, so plans in two terminals don't clash.
ip_port = "127.0.0.1:0"
# hide = ["locals", "variables"]
show_sensitive = false
"""

LOCAL_TEMPLATE = """\
# tfroam settings for this directory and everything under it. They override the global config.

[auto]
# false turns tfroam off here even when the global config turns it on.
enabled = true

# [view]
# mode = "page"
# hide = ["locals", "variables"]
"""


def describe(settings: Settings) -> str:
    v = settings.view
    lines = [
        f"Root:          {settings.root}",
        f"Global config: {settings.global_path}{'' if settings.global_path.is_file() else '  (not found)'}",
        f"Local config:  {settings.local_path or f'(none: no {LOCAL_NAME} here or in a parent)'}",
        f"Auto:          {'on' if settings.enabled else 'off'} ({settings.reason})",
        f"View:          mode={v.mode} open_browser={str(v.open_browser).lower()} ip_port={v.ip_port}"
        f" hide={','.join(v.hide) or '-'} show_sensitive={str(v.show_sensitive).lower()}",
    ]
    return "\n".join(lines) + "\n"


def config_init(target: Path, template: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(target, "x", encoding="utf-8") as fh:
            fh.write(template)
    except FileExistsError as exc:
        raise ConfigError(f"{target} already exists; edit it instead") from exc


USAGE = """\
Usage:
  tfroam plan [-chdir=DIR] [plan options]   run `terraform plan`, then open the plan in tfroam
  tfroam shell-init zsh|bash|fish           print the shell hook that opens plans automatically
  tfroam config [DIR]                       show the settings that apply to DIR (default: here)
  tfroam config init [--global]             write a starter .tfroam.toml here (or the global config)
  tfroam [flags]                            the visualizer itself: `tfroam -h` lists its flags

TFROAM_AUTO=0|1 turns automatic opening off or on for one command; TFROAM_TF=tofu makes
`tfroam plan` use OpenTofu.
"""


def main(argv: Sequence[str], env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    name, rest = argv[0], list(argv[1:])
    try:
        if name == "plan":
            binary = env.get(TF_ENV) or "terraform"
            global_args = [a for a in rest if a.startswith("-chdir=")]
            command = TfCommand(global_args, "plan", [a for a in rest if not a.startswith("-chdir=")])
            if wants_plain_terraform(command.args):
                return _exec(binary, [*command.global_args, "plan", *command.args])
            settings = resolve(command.chdir or ".", env)
            return plan_and_view(binary, command, settings)

        if name == "hook":
            if not rest:
                sys.stderr.write("tfroam hook: expected the Terraform binary, as the shell hook passes it\n")
                return 2
            binary, args = rest[0], rest[1:]
            command = split_command(args)
            if command.subcommand != "plan" or wants_plain_terraform(command.args):
                return _exec(binary, args)
            try:
                settings = resolve(command.chdir or ".", env)
            except ConfigError as exc:
                sys.stderr.write(f"tfroam: {exc} (running plain {binary})\n")
                return _exec(binary, args)
            forced = env.get(AUTO_ENV, "").strip().lower() in _TRUE
            if not settings.enabled or not (forced or interactive(env)):
                return _exec(binary, args)
            return plan_and_view(binary, command, settings)

        if name == "shell-init":
            shell = rest[0] if rest else os.path.basename(env.get("SHELL", ""))
            sys.stdout.write(shell_init(shell))
            return 0

        if name == "config":
            if rest[:1] == ["init"]:
                if rest[1:] == ["--global"]:
                    target, template = global_config_path(env), GLOBAL_TEMPLATE
                elif not rest[1:]:
                    target, template = Path.cwd() / LOCAL_NAME, LOCAL_TEMPLATE
                else:
                    sys.stderr.write(USAGE)
                    return 2
                config_init(target, template)
                sys.stdout.write(f"Wrote {target}\n")
                return 0
            if len(rest) > 1:
                sys.stderr.write(USAGE)
                return 2
            sys.stdout.write(describe(resolve(rest[0] if rest else ".", env)))
            return 0
    except TfroamError as exc:
        sys.stderr.write(f"tfroam: {exc}\n")
        return 1
    sys.stderr.write(USAGE)
    return 2
