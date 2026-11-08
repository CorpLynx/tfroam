# tfroam

**tfroam** is an interactive [Terraform](https://www.terraform.io/) visualizer: a Python port of
[im2nguyen/rover](https://github.com/im2nguyen/rover). The Go backend is rewritten in Python and the
original Vue frontend is bundled unchanged, so you get the same visualization.

The package is `tfroam`; the command is `rover` (as in the original, so existing scripts and Docker
commands keep working) and `tfroam` is an alias for it.

Rover:

1. runs `terraform init` and `terraform plan` (or reads a plan you give it) and parses the `.tf`
   configuration in the working directory,
2. builds three documents from them: the resource overview (`rso`), the module/file map (`map`) and the
   resource graph (`graph`),
3. serves an interactive configuration and state visualization on `0.0.0.0:9000`, writes a standalone
   `rover.zip`, or saves the graph as an SVG.

![Rover screenshot](docs/screenshot.png)

## Install

tfroam needs **Python 3.11+** and a **Terraform** binary (unless you only visualize a plan file). It does
not need Docker, and the frontend is prebuilt, so it does not need Node either.

```
$ pipx install tfroam        # or: pip install tfroam
```

From a checkout:

```
$ pip install .
```

Rover looks for `terraform` on your `PATH`. Use `-tfPath` to point at a specific binary.

### Image export (optional)

`-genImage` renders the page in a headless browser, which needs Playwright and a Chromium:

```
$ pipx install 'tfroam[image]'
$ playwright install chromium      # or set ROVER_BROWSER_PATH to an existing Chrome/Chromium
```

## Quickstart

Run it in any Terraform working directory:

```
$ rover
2026/11/06 06:46:23 Starting Rover...
2026/11/06 06:46:23 Initializing Terraform...
2026/11/06 06:46:24 Generating plan...
2026/11/06 06:46:25 Generating resource overview...
2026/11/06 06:46:25 Generating resource map...
2026/11/06 06:46:25 Generating resource graph...
2026/11/06 06:46:25 Done generating assets.
2026/11/06 06:46:25 Rover is running on 0.0.0.0:9000
```

Then open <http://localhost:9000>. Other directory? `rover -workingDir example/eks-cluster`.

### Run on a Terraform plan file

Use `-planJSONPath` to visualize a plan without running Terraform (the binary is not needed):

```
$ terraform plan -out plan.out
$ terraform show -json plan.out > plan.json
$ rover -planJSONPath plan.json
```

Or give Rover the binary plan with `-planPath plan.out`.

### Standalone mode

Writes `rover.zip` with every static asset; unzip it and open `index.html` straight from disk:

```
$ rover -standalone true
```

### Save an image

Writes `./rover.svg`:

```
$ rover -genImage true
```

### Making a big graph easier to read

Almost every resource references a local or a variable, so those produce most of the lines. Hide them (and
every line touching them) with `-hide`; it works for the live view, `-standalone` and `-genImage`:

```
$ rover -hide locals,variables              # resources, modules and outputs
$ rover -hide locals,variables,outputs      # just resources and what depends on what
```

On a three-account AWS network (61 resources) the first removes about half of the 153 lines and the second
about 60%. The module/file explorer is unaffected. This is an addition to the original Rover; without
`-hide` the output is identical to it.

### Variables, backends and workspaces

```
$ rover -tfBackendConfig test.tfbackend -tfVarsFile test.tfvars -tfVar max_length=4 -workspaceName dev
```

Environment variables (cloud credentials, `TF_VAR_*`, `TF_CLI_ARGS`) are passed through to Terraform.

## Docker

The image keeps the original contract, so the commands from the upstream README work unchanged. Build it
once (there is no published image for the port):

```
$ docker build . -t im2nguyen/rover
```

```
$ docker run --rm -it -p 9000:9000 -v "$(pwd):/src" im2nguyen/rover
$ docker run --rm -it -p 9000:9000 -v "$(pwd)/plan.json:/src/plan.json" im2nguyen/rover -planJSONPath=plan.json
$ docker run --rm -it -p 9000:9000 -v "$(pwd):/src" im2nguyen/rover -standalone true
$ docker run --rm -it -p 9000:9000 -v "$(pwd):/src" --env-file ./.env im2nguyen/rover
$ docker run --rm -it -v "$(pwd):/src" im2nguyen/rover -genImage true
```

The image is Debian-based Python (`python:3.12-slim`, pinned by digest) with `/bin/terraform` (copied from
the official Terraform image) and Chromium. Pick a Terraform version with `--build-arg TF_VERSION=1.9.8`;
the default, 1.5.5, is upstream's and the last MPL-2.0 Terraform release.

The entrypoint is `rover -ipPort 0.0.0.0:9000`, so the server listens on all interfaces and `-p 9000:9000`
works. Outside a container the default is `127.0.0.1:9000`. Your flags are appended after the entrypoint, and
a later `-ipPort` overrides it, e.g. `docker run ... im2nguyen/rover -ipPort 0.0.0.0:8080` with `-p 8080:8080`.

## Flags

The same flags, spelled the same way, as the Go version (`-flag value`, `-flag=value` and `--flag` all work).

| Flag | Meaning | Default |
|---|---|---|
| `-workingDir` | Directory with the Terraform configuration | `.` |
| `-tfPath` | Terraform binary | `terraform` on `PATH`, else `/bin/terraform` |
| `-planPath` | Existing binary plan file | |
| `-planJSONPath` | Existing plan in JSON (`terraform show -json`) | |
| `-tfBackendConfig` | Backend config file for `init` (repeatable) | |
| `-tfVarsFile` | tfvars file for `plan` (repeatable) | |
| `-tfVar` | `key=value` variable for `plan` (repeatable) | |
| `-workspaceName` | Workspace to select | |
| `-showSensitive` | Show sensitive output values | `false` |
| `-hide` | Hide kinds of nodes in the graph: `locals`, `variables`, `outputs` (comma-separated or repeated) | |
| `-standalone` | Write `<zipFileName>.zip` and exit | `false` |
| `-zipFileName` | Name of the standalone zip (without `.zip`) | `rover` |
| `-genImage` | Save `./rover.svg` and exit | `false` |
| `-ipPort` | Address to serve on | `0.0.0.0:9000` |
| `-name` | Configuration name | `rover` |
| `-version` | Print the version | |

## Differences from the Go version

- **Install:** a Python package (`pip`/`pipx`) instead of a single static binary.
- **Terraform lookup:** without `-tfPath`, Rover uses `terraform` from `PATH` (falling back to the Go
  default `/bin/terraform`), so it works outside a container. A missing Terraform only matters when Rover
  has to run it: `-planJSONPath` works without it.
- **Terraform Cloud flags** (`-tfcOrg`, `-tfcWorkspace`, `-tfcNewRun`) are accepted but not supported; they
  exit with an error.
- **`.tf.json` files** are not read for file and line information (the plan still shows them).
- **Booleans:** `-standalone true` and `-standalone false` both work as written; Go treats a following
  `false` as a stray argument.
- **Graph ordering:** nodes and edges are listed in a stable order. Go's order changes from run to run;
  the content is the same.
- **Image mode:** needs the `image` extra and a Chromium (see above). Image layouts are not byte-for-byte
  reproducible, in Go either; the elements, labels and canvas size match.
- **Terraform output** is captured, as in the original, and shown only if a command fails.

More detail, including every quirk deliberately kept, is in [PORTING_NOTES.md](PORTING_NOTES.md).

## Development

```
$ pip install -e '.[dev,image]'
$ ruff check . && ruff format --check . && mypy --strict tfroam tests && pytest
```

The tests compare Rover's output with files produced by the original Go program
(`tests/golden/`). To also run the test that uses a real Terraform, set `TERRAFORM_BIN` to its path.
`scripts/build_ui.sh` rebuilds the bundled frontend from the pinned upstream commit (needs Node).

## License

MIT, as upstream. The bundled frontend and the file formats come from
[im2nguyen/rover](https://github.com/im2nguyen/rover); see [LICENSE](LICENSE).
