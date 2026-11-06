"""Command line: same flags, spelling and defaults as the Go version.

Go's ``flag`` package accepts ``-name value``, ``-name=value`` and ``--name``, and a
bare boolean flag means true. argparse can't express single-dash camelCase names,
so flags are parsed here, following the Go package's rules.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, TextIO

from . import VERSION
from .assets import RoverError, generate_assets
from .export import generate_zip

log = logging.getLogger("rover")

Kind = Literal["str", "bool", "list"]


@dataclass(frozen=True)
class FlagSpec:
    name: str
    kind: Kind
    default: str | bool
    help: str


FLAGS: tuple[FlagSpec, ...] = (
    FlagSpec("tfPath", "str", "/bin/terraform", "Path to Terraform binary"),
    FlagSpec("workingDir", "str", ".", "Path to Terraform configuration"),
    FlagSpec("name", "str", "rover", "Configuration name"),
    FlagSpec("zipFileName", "str", "rover", "Standalone zip file name"),
    FlagSpec("ipPort", "str", "0.0.0.0:9000", "IP and port for Rover server"),
    FlagSpec("planPath", "str", "", "Plan file path"),
    FlagSpec("planJSONPath", "str", "", "Plan JSON file path"),
    FlagSpec("workspaceName", "str", "", "Workspace name"),
    FlagSpec("tfcOrg", "str", "", "Terraform Cloud Organization name"),
    FlagSpec("tfcWorkspace", "str", "", "Terraform Cloud Workspace name"),
    FlagSpec("standalone", "bool", False, "Generate standalone HTML files"),
    FlagSpec("showSensitive", "bool", False, "Display sensitive values"),
    FlagSpec("tfcNewRun", "bool", False, "Create new Terraform Cloud run"),
    FlagSpec("version", "bool", False, "Get current version"),
    FlagSpec("genImage", "bool", False, "Generate graph image"),
    FlagSpec("tfVarsFile", "list", "", "Path to *.tfvars files"),
    FlagSpec("tfVar", "list", "", "Terraform variable (key=value)"),
    FlagSpec("tfBackendConfig", "list", "", "Path to *.tfbackend files"),
)
_BY_NAME = {f.name: f for f in FLAGS}


class UsageError(Exception):
    pass


@dataclass
class Options:
    tf_path: str = "/bin/terraform"
    working_dir: str = "."
    name: str = "rover"
    zip_file_name: str = "rover"
    ip_port: str = "0.0.0.0:9000"
    plan_path: str = ""
    plan_json_path: str = ""
    workspace_name: str = ""
    tfc_org: str = ""
    tfc_workspace: str = ""
    standalone: bool = False
    show_sensitive: bool = False
    tfc_new_run: bool = False
    version: bool = False
    gen_image: bool = False
    tf_vars_files: list[str] = field(default_factory=list)
    tf_vars: list[str] = field(default_factory=list)
    tf_backend_configs: list[str] = field(default_factory=list)


_ATTRS = {
    "tfPath": "tf_path",
    "workingDir": "working_dir",
    "name": "name",
    "zipFileName": "zip_file_name",
    "ipPort": "ip_port",
    "planPath": "plan_path",
    "planJSONPath": "plan_json_path",
    "workspaceName": "workspace_name",
    "tfcOrg": "tfc_org",
    "tfcWorkspace": "tfc_workspace",
    "standalone": "standalone",
    "showSensitive": "show_sensitive",
    "tfcNewRun": "tfc_new_run",
    "version": "version",
    "genImage": "gen_image",
    "tfVarsFile": "tf_vars_files",
    "tfVar": "tf_vars",
    "tfBackendConfig": "tf_backend_configs",
}


def usage_text(prog: str = "rover") -> str:
    lines = [f"Usage of {prog}:"]
    for spec in sorted(FLAGS, key=lambda f: f.name):
        head = f"  -{spec.name}" if spec.kind == "bool" else f"  -{spec.name} string"
        text = spec.help
        if spec.kind == "str" and spec.default:
            text += f' (default "{spec.default}")'
        lines += [head, f"    \t{text}"]
    return "\n".join(lines) + "\n"


def _bool_value(text: str, flag: str) -> bool:
    lowered = text.lower()
    if lowered in ("1", "t", "true"):
        return True
    if lowered in ("0", "f", "false"):
        return False
    raise UsageError(f'invalid boolean value "{text}" for -{flag}')


def parse_args(argv: Sequence[str]) -> Options:
    """Parse like Go's flag package: parsing stops at the first non-flag argument."""
    opts = Options()
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if len(arg) < 2 or not arg.startswith("-"):
            break
        i += 1
        if arg == "--":
            break
        name = arg[2:] if arg.startswith("--") else arg[1:]
        if not name or name.startswith(("-", "=")):
            raise UsageError(f"bad flag syntax: {arg}")
        name, has_value, value = name.partition("=")
        if name in ("h", "help"):
            raise UsageError("help")
        spec = _BY_NAME.get(name)
        if spec is None:
            raise UsageError(f"flag provided but not defined: -{name}")
        attr = _ATTRS[name]
        if spec.kind == "bool":
            if has_value:
                setattr(opts, attr, _bool_value(value, name))
            elif i < len(args) and args[i].lower() in ("true", "false"):
                # README usage is `-standalone true`; Go would treat "true" as a stray argument.
                setattr(opts, attr, args[i].lower() == "true")
                i += 1
            else:
                setattr(opts, attr, True)
            continue
        if not has_value:
            if i >= len(args):
                raise UsageError(f"flag needs an argument: -{name}")
            value = args[i]
            i += 1
        if spec.kind == "list":
            getattr(opts, attr).append(value)
        else:
            setattr(opts, attr, value)
    return opts


def split_list(values: Sequence[str]) -> list[str]:
    """Go joins repeated flags with "," and splits again, so commas separate values (a Go quirk, kept)."""
    return [part for part in ",".join(values).split(",") if part]


def _absolute(path: str) -> str:
    if not path or path.startswith("/") or os.path.isabs(path):
        return path
    return os.path.join(os.getcwd(), path)


def configure_logging(stream: TextIO | None = None) -> None:
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y/%m/%d %H:%M:%S"))
    log.handlers[:] = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False


def run(opts: Options) -> int:
    if opts.version:
        print(f"Rover v{VERSION}")
        return 0

    log.info("Starting Rover...")

    if opts.tfc_org or opts.tfc_workspace or opts.tfc_new_run:
        raise RoverError("Terraform Cloud flags (-tfcOrg, -tfcWorkspace, -tfcNewRun) are not supported in this port")

    assets = generate_assets(
        working_dir=opts.working_dir,
        tf_path=opts.tf_path,
        plan_path=_absolute(opts.plan_path),
        plan_json_path=_absolute(opts.plan_json_path),
        workspace_name=opts.workspace_name,
        tf_vars_files=split_list(opts.tf_vars_files),
        tf_vars=split_list(opts.tf_vars),
        tf_backend_configs=split_list(opts.tf_backend_configs),
        show_sensitive=opts.show_sensitive,
    )
    log.info("Done generating assets.")

    if opts.standalone:
        filename = f"{opts.zip_file_name}.zip"
        try:
            generate_zip(assets, filename)
        except OSError as exc:
            raise RoverError(str(exc)) from exc
        log.info("Generated zip file: %s", filename)
        return 0

    from .server import start_server

    if opts.gen_image:
        from .screenshot import INSTALL_HINT, playwright_available

        if not playwright_available():
            raise RoverError(INSTALL_HINT)

    try:
        server = start_server(opts.ip_port, assets)
    except (OSError, ValueError) as exc:
        raise RoverError(f"Could not start server: {exc}") from exc

    image_error: list[Exception] = []
    if opts.gen_image:
        from .screenshot import ImageError, screenshot

        def take_screenshot() -> None:
            try:
                screenshot(opts.ip_port)
            except ImageError as exc:
                image_error.append(exc)
            finally:
                server.shutdown()  # like Go: stop serving once the image is saved (or failed)

        threading.Thread(target=take_screenshot, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

    if opts.gen_image:
        if image_error:
            raise RoverError(str(image_error[0]))
        log.info("Server shut down.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    try:
        opts = parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as exc:
        if str(exc) == "help":
            sys.stderr.write(usage_text())
            return 0
        sys.stderr.write(f"{exc}\n{usage_text()}")
        return 2
    try:
        return run(opts)
    except RoverError as exc:
        log.error("%s", exc)
        return 1
