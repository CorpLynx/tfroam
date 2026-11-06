# Porting notes

Upstream: https://github.com/im2nguyen/rover @ 6257a19a32c0116eae05c91e3c295d07c54c3a05

## Spec vs. Go code

- Backend output is JavaScript, not JSON: `plan.js`, `rso.js`, `map.js`, `graph.js`
  (`const rso = {...}` etc.), emitted into the standalone zip. Goldens are those files.
- Extra flags in Go: `-tfcOrg`, `-tfcWorkspace`, `-tfcNewRun` (Terraform Cloud; not ported),
  `-showSensitive`, `-version` (to be ported). Spec's `-tfConfigExists` does not exist.
- Defaults: `-tfPath=/bin/terraform`, `-workspaceName=""`.

## Golden capture

- Go 1.24.7, Terraform 1.9.8, built from the pinned upstream commit.
- Providers installed from a filesystem mirror (random 3.1.0 from releases.hashicorp.com)
  because the sandbox blocks registry.terraform.io.
- random-test golden: `data "http"` URL changed from checkpoint-api.hashicorp.com
  (blocked in the sandbox) to a releases.hashicorp.com URL. Only plan-time values differ.
- `tests/fixtures/*` holds the `.tf` sources and `.terraform/modules` metadata used.

## Step 3: plan models + Terraform runner

- `rover/plan.py` mirrors terraform-json v0.13.0, not the current Terraform plan schema. Fields that
  version doesn't know (`applyable`, `complete`, `errored`, `timestamp`, `relevant_attributes`,
  provider `full_name`, output `type`, `action_reason`, ...) are dropped on load, as Go does, and the
  goldens expect that.
- Go `omitempty` quirks reproduced: explicit JSON nulls in `interface{}` fields vanish (an expression
  with `constant_value: null` serializes as `{}`), `change.before` and `schema_version` are always
  emitted, `false`/`0`/`""` inside `interface{}` fields are kept.
- `rover/terraform.py` follows terraform-exec v0.15.0: `init -no-color -force-copy -input=false
  -backend=true -get=true -upgrade=true`, `plan ... -detailed-exitcode` (exit 2 = success), then
  `show -json -no-color`. TF_VAR_*/TF_CLI_ARGS pass through from the caller's environment;
  TF_LOG, TF_LOG_PATH, TF_WORKSPACE are blanked and TF_IN_AUTOMATION=1 is set.
- Terraform output is captured, not streamed (as in Go); stderr is included in the error message.
- Terraform Cloud (`-tfcOrg`, `-tfcWorkspace`, `-tfcNewRun`) is not ported.

## Added fixtures (captured from the Go binary)

- `for-each-test`: `for_each` over a set and a map with string keys, a resource iterating another
  resource, and a module with `for_each`.
- `deep-nested-test`: local modules three levels deep (`level1.level2.level3`, the last with `count`)
  plus one git-sourced module. **No registry module**: the sandbox blocks registry.terraform.io, so a
  git source stands in. `modules.json` shape for registry sources is therefore untested; add one
  fixture when registry access is available.
- `actions-test`: real prior state, then a config edit giving create, update, delete, replace
  (`delete`+`create`) and no-op. `main.tf.initial` is the config used to build the state.
- `plan-only-test`: the `for-each-test` plan fed through `-planJSONPath` with an empty working
  directory (`plan.json`; create an empty dir at test time). Go logs "Continuing without loading
  module from filesystem: per_region" for the module call in this mode.
- The `.terraform/modules/remote` fixture dir is trimmed to the one `.tf` file the module uses.

## Step 4a: rso (`rover/rso.py`) and config loader (`rover/config.py`)

- `rso.go` embeds `tfconfig.Module` (terraform-config-inspect) in the overview, so a port of that
  loader was required: `rover/config.py`. It uses python-hcl2's **parse tree** (not its dict output,
  which can't tell bare object keys from strings and mangles heredocs) for block start lines, and a
  small constant evaluator for `default`, `description`, `sensitive`, `source`, `version`, etc.
  Output matches the Go goldens for all 16 module configs across the 8 fixtures.
- Faithful quirks: a variable `default` that isn't a constant makes the whole module "unloadable"
  (Go emits an error diagnostic and rover logs "Continuing without loading module from
  filesystem"); `default` is always emitted (`null` when absent); `required_providers` entries with
  an object that has no keys are dropped; resources with no `provider` get the type prefix.
- Gaps vs the Go loader: `*.tf.json` files (reported as an error, module not loaded), the legacy-HCL
  fallback parser, and constant arithmetic like `1 + 2` in a default.
- Go panics when an output's `before_sensitive`/`after_sensitive` is not a bool (object-shaped
  sensitivity). The port ignores non-bool markers instead; the value is left unmasked.
- `generate_resource_overview` mutates `plan.output_changes` in place, as Go does; `plan.js` shows
  the "Sensitive Value" placeholder because the plan is serialized afterwards.
- `StateResource.values` is now `None` when absent (Go nil map) so empty-vs-missing matches.

## Step 4b: map (`rover/map.py`)

- Faithful quirks kept: `line` and `required` are pointers in Go, so they're emitted even when `0`/`false`;
  `AddFileIfNotExists` builds `source` as `"<module source>/<file>"`, and module nodes have no source, so
  file nodes under a module get `"/main.tf"`; locals are collected only from resource and module-call
  expressions (not data sources, and not from nested blocks); outputs/variables fall back to the plan's
  configuration only when the module's `.tf` files could not be loaded.
- New fixture `noconfig-test`: a working dir whose `main.tf` has a syntax error, so rover logs "Could not
  load configuration" and puts everything under "unknown file". (Go refuses a working dir that doesn't
  exist at all: "error initialising Terraform with workdir".)

## Step 4c: graph (`rover/graph.py`)

- Go iterates maps in random order, so node/edge **order** in its `graph.js` differs from run to run
  (content does not). The port iterates sorted keys, so its output is reproducible; tests compare
  nodes and edges by id. The frontend only reads the lists as sets.
- Kept as-is: edges are made only for whole-resource references (exactly two dotted parts) and
  data-source references (three parts); `each.*` references are skipped; `var.`/`output.`/`local.`/
  `module.`/`data.` colours follow the substring order in `addEdges`; a `terraform.workspace` reference
  adds a synthetic `locals` node parented to the base path.
- Go would panic on an output whose configuration has no expression; the port skips it.

## Step 5: server, standalone zip, CLI

- **Frontend:** `rover/ui/dist` is upstream's committed `ui/dist` at the pinned commit, verbatim
  (MIT; see `LICENSE.rover`). A fresh `npm ci && npm run build` of the upstream source in this sandbox
  produced the same code, same file sizes and identical vendor/CSS bundles; only `app.js` got a different
  content hash (it only changes the `sourceMappingURL` comment). `scripts/build_ui.sh` redoes the build.
- **Zip:** every frontend file in the generated zip is byte-identical to the Go zip's (file order too),
  including the `index.html` rewrite. Go's `strings.Split(html, "</head>")` drops the closing `</head>`
  tag and the three injected script tags are separated by `\n\t\t`; both are reproduced. `plan.js` is
  written but `index.html` does not load it (as in Go). The data files are JSON-equivalent to Go's;
  `graph.js` list order differs (see graph notes).
- **Server:** stdlib `ThreadingHTTPServer`, no new dependency. `/health`, `/api/{plan,rso,map,graph}`
  with `Access-Control-Allow-Origin: *`, an unknown `/api/x` answers 200 with Go's help text. The Go
  version sets `Content-Type` after writing, so it is ignored and browsers sniff it; the port sends
  `application/json` properly. Requests are not logged (Go doesn't either).
- **CLI:** Go-`flag` syntax (`-name value`, `-name=value`, `--name`, parsing stops at the first
  non-flag). A bare bool flag is true; the README's `-standalone true` / `-genImage true` work because a
  following literal `true`/`false` is consumed (Go would treat `false` as a stray argument and still set
  the flag true: the port sets it false). Repeated `-tfVar`/`-tfVarsFile`/`-tfBackendConfig` are joined with
  `,` and re-split like Go, so a value containing a comma is split (Go bug, kept).
- `-tfcOrg`, `-tfcWorkspace`, `-tfcNewRun` are accepted but exit 1 with a clear message.
- `-genImage` is accepted but not implemented until step 6.
- **Terraform binary check:** like terraform-exec, only the working directory (must exist) and a
  non-empty `-tfPath` are validated up front; a missing binary only fails when a command runs, so
  `-planJSONPath` works without Terraform installed.
- Verified in headless Chromium: the served page and the unzipped `index.html` opened from `file://`
  both render the random-test graph with no console errors.

## Step 6: image mode (`rover/screenshot.py`)

- Same flow as chromedp: open the served page, wait for `#cytoscape-div`, click `#saveGraph` (the UI
  downloads `rover.svg`), save the download to `./rover.svg` (the Go version ignores `-name` here and
  always writes `./rover.svg`), log "Image generation complete.", then shut the server down and log
  "Server shut down.". 60 s overall timeout. A failure exits 1.
- **Differs from the spec text, follows Go:** the server listens on `-ipPort` (default `0.0.0.0:9000`),
  not a free port; the browser is pointed at it (a wildcard host becomes `127.0.0.1`, since `0.0.0.0` is not
  connectable everywhere). The spec's "free port in a background thread" is not what Go does.
- Playwright is optional (`pip install 'tfroam[image]'`); without it `-genImage` fails fast, before the server
  starts, with an install hint. Browser: `ROVER_BROWSER_PATH` if set, else Playwright's own Chromium, else a
  system `chromium`/`google-chrome` on `PATH` (what the Docker image uses). `--no-sandbox` is added when
  running as root.
- **SVG comparison:** the layout is not reproducible even in Go (two Go runs on the same input differ in
  coordinates and gradient ids, because of Go's random map order). What *is* stable and what the test
  checks against the committed Go reference `tests/golden/random-test/rover.svg`: element counts per tag,
  all text labels, and the canvas size. The Python SVG matches the Go SVG on all three.
- Repo hygiene: 25 `__pycache__/*.pyc` files had been committed by mistake in earlier steps; they are now
  untracked and `.gitignore` is in place.

## Step 7: packaging, Docker, CI, running without a container

- **Native install is a supported path**, not just Docker: `pip`/`pipx install tfroam`. Verified in a clean
  virtualenv outside the source tree: the wheel carries the UI (`rover/ui/dist`, 20 files), `py.typed`, the
  licence and the `rover` console script; only `python-hcl2`, `lark` and `regex` are pulled in (no Node,
  no Playwright unless `[image]`).
- **`-tfPath` default:** Go's `/bin/terraform` only exists in the container. Without `-tfPath` the port
  uses `terraform` from `PATH` and falls back to `/bin/terraform`; a bare `-tfPath terraform` is looked up
  on `PATH`; anything with a directory part is used as given. Verified with a real Terraform on `PATH`.
- **Dependencies:** `python-hcl2>=8.1.4,<9` (named in the spec) and `lark` (its parser library, imported
  directly because `config.py` reads the parse tree, so it is declared rather than relied on
  transitively). The `<9` cap is deliberate: the tree shape is python-hcl2 internals.
- **Windows:** JSON paths are normalised to forward slashes (`Module.path`, module locations, file
  positions). I could only check this logic by unit test; the Windows CI job is the real check.
  The fake-Terraform shell-script tests are skipped on Windows. `.gitattributes` pins LF line endings for
  fixtures and goldens and leaves the bundled UI untouched (`-text`).
- **Docker:** Debian-based `python:3.12-slim` (Playwright has no musl/Alpine wheels, so the upstream
  Alpine base would not work) with `/bin/terraform` copied from `hashicorp/terraform:$TF_VERSION`,
  Chromium, git and openssh (module sources), `WORKDIR /src`, `ENTRYPOINT ["rover"]`. No Node stage: the
  frontend is already built. **The image has not been built in the sandbox (no Docker daemon); the CI
  `docker` job builds it and runs the README command shapes, including `-genImage`.**
- **CI** (`.github/workflows/ci.yml`): ruff, `ruff format --check`, `mypy --strict`, pytest on
  Linux/macOS/Windows x Python 3.11/3.13 (real-Terraform test enabled on Linux/macOS); a real-browser job
  where the screenshot tests must not skip; a wheel build + clean-venv install job; a Docker job.
- **Distribution name:** `tfroam` (PyPI name `rover` is taken; `tfroam` was free when checked, but
  nothing is published yet, so re-check before the first upload). The import package stays `rover` (as the
  spec names it) and the console command stays `rover` so the README, Docker entrypoint and existing
  scripts are unchanged; `tfroam` is installed as a second command for the same program. Caveat: any
  other installed distribution that also provides a top-level `rover` module would collide with this one.
  Renaming the import package to `tfroam` would remove that risk at the cost of touching every module.
- Repo hygiene: `.gitignore` anchors `/dist/` and `/build/` so it cannot hide `rover/ui/dist`.
