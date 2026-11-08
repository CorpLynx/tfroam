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
3. serves an interactive configuration and state visualization on `127.0.0.1:9000`, writes a standalone
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
2026/11/06 06:46:25 Rover is running on 127.0.0.1:9000
```

Then open <http://localhost:9000>. Other directory? `rover -workingDir example/eks-cluster`.

Rover only listens on `127.0.0.1`. To reach it from another machine, ask for it explicitly with
`rover -ipPort 0.0.0.0:9000`. If `-ipPort` is given more than once, the last one wins.

Terraform's own output is captured and only shown if a command fails, so a long `init` can look stuck. Add
`-verbose` to watch `init`, `workspace select` and `plan` as they run (on stderr):

```
$ rover -verbose
```

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

Writes `./rover.svg`, or the file named by `-imagePath`:

```
$ rover -genImage true
$ rover -genImage -imagePath docs/graph.svg
```

While it renders, image mode serves the page on a free port on `127.0.0.1`, so it works even when another
Rover is already running on port 9000. Give `-ipPort` to choose the address yourself.

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

### What Rover does to your working directory

Rover runs `terraform init -input=false -upgrade=false`, so your `.terraform.lock.hcl` is left as it is.
Pass `-tfUpgrade` to run `init -upgrade=true` instead (newest allowed provider and module versions, lock
file rewritten). Rover never migrates state: if the backend configuration changed since the last `init`,
Terraform stops with "Backend configuration changed" and you run `terraform init -migrate-state` (or
`-reconfigure`) yourself.

Like `terraform plan`, Rover's `plan` takes the state lock and refreshes state. On shared remote state you
can skip either or both:

```
$ rover -tfLock=false -tfRefresh=false
```

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

The image is Debian-based Python with `/bin/terraform` (copied from the official Terraform image) and
Chromium. Pick a Terraform version with `--build-arg TF_VERSION=1.9.8`.

## Flags

The Go version's flags, spelled the same way, plus a few additions (`-flag value`, `-flag=value` and
`--flag` all work). A bare boolean flag means true; turn one off with `-flag=false` or `-flag false`.

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
| `-showSensitive` | Show sensitive values (resource attributes, state, outputs, variable defaults) | `false` |
| `-hide` | Hide kinds of nodes in the graph: `locals`, `variables`, `outputs` (comma-separated or repeated) | |
| `-standalone` | Write `<zipFileName>.zip` and exit | `false` |
| `-zipFileName` | Name of the standalone zip (without `.zip`) | `rover` |
| `-genImage` | Save the graph as an SVG (see `-imagePath`) and exit | `false` |
| `-imagePath` | Where `-genImage` writes the SVG | `rover.svg` |
| `-ipPort` | Address to serve on (if repeated, the last one wins) | `127.0.0.1:9000`; with `-genImage`, a free port on `127.0.0.1` |
| `-tfUpgrade` | Run `init -upgrade=true` (updates `.terraform.lock.hcl`) | `false` |
| `-tfLock` | Take the state lock during `plan`; `-tfLock=false` skips it | `true` |
| `-tfRefresh` | Refresh state during `plan`; `-tfRefresh=false` skips it | `true` |
| `-verbose` | Stream Terraform's `init`, `workspace select` and `plan` output to stderr | `false` |
| `-name` | Configuration name | `rover` |
| `-version` | Print the version | |

## Differences from the Go version

- **Install:** a Python package (`pip`/`pipx`) instead of a single static binary.
- **Terraform lookup:** without `-tfPath`, Rover uses `terraform` from `PATH` (falling back to the Go
  default `/bin/terraform`), so it works outside a container; `-h` says so. A missing Terraform only
  matters when Rover has to run it: `-planJSONPath` works without it.
- **Bind address:** the default `-ipPort` is `127.0.0.1:9000` (Go: `0.0.0.0:9000`), so the view of your
  plan is not exposed on the network unless you pass `-ipPort 0.0.0.0:9000`. The Docker image passes it.
- **`init` and `plan`:** `init` runs with `-upgrade=false` (Go: `-upgrade=true`, which rewrites
  `.terraform.lock.hcl` on every run; use `-tfUpgrade` for that) and without `-force-copy` (Go silently
  migrates state when the backend changed; here Terraform stops with its own error). `-tfLock` and
  `-tfRefresh` control `plan -lock`/`-refresh`, which Go always sets to `true`.
- **Early checks:** a bad `-hide` value or a missing Playwright for `-genImage` fails before Terraform runs.
- **Terraform Cloud flags** (`-tfcOrg`, `-tfcWorkspace`, `-tfcNewRun`) are accepted but not supported; they
  exit with an error.
- **`.tf.json` files** are not read for file and line information (the plan still shows them).
- **Booleans:** `-standalone true` and `-standalone false` both work as written; Go treats a following
  `false` as a stray argument.
- **Graph ordering:** nodes and edges are listed in a stable order. Go's order changes from run to run;
  the content is the same.
- **Image mode:** needs the `image` extra and a Chromium (see above). Without `-ipPort` it serves on a free
  port on `127.0.0.1` (Go uses `-ipPort`, so it fails if port 9000 is taken), and `-imagePath` sets the
  output file (Go always writes `./rover.svg`). Image layouts are not byte-for-byte reproducible, in Go
  either; the elements, labels and canvas size match.
- **Terraform output** is captured, as in the original, and shown only if a command fails; `-verbose`
  streams it live.
- **Sensitive values are hidden everywhere**, not just in outputs: sensitive resource attributes (before
  and after), state values, object-shaped output markers and `sensitive = true` variable defaults all
  show as `Sensitive Value` unless `-showSensitive` is given.
- **No full plan is published:** `/api/plan` is gone and the standalone zip has no `plan.js` (the UI never
  read either).
- **API responses have no `Access-Control-Allow-Origin: *` header**; the UI is same-origin.
- **DNS-rebinding guard:** on a loopback `-ipPort` (e.g. `127.0.0.1:9000`), requests with any `Host` other
  than `localhost`, `127.0.0.1`, `[::1]` or the bound host get `403`. A wildcard bind such as Docker's
  `0.0.0.0` is not filtered.

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
