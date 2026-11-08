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

- `tfroam/plan.py` mirrors terraform-json v0.13.0, not the current Terraform plan schema. Fields that
  version doesn't know (`applyable`, `complete`, `errored`, `timestamp`, `relevant_attributes`,
  provider `full_name`, output `type`, `action_reason`, ...) are dropped on load, as Go does, and the
  goldens expect that.
- Go `omitempty` quirks reproduced: explicit JSON nulls in `interface{}` fields vanish (an expression
  with `constant_value: null` serializes as `{}`), `change.before` and `schema_version` are always
  emitted, `false`/`0`/`""` inside `interface{}` fields are kept.
- `tfroam/terraform.py` follows terraform-exec v0.15.0: `init -no-color -force-copy -input=false
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

## Step 4a: rso (`tfroam/rso.py`) and config loader (`tfroam/config.py`)

- `rso.go` embeds `tfconfig.Module` (terraform-config-inspect) in the overview, so a port of that
  loader was required: `tfroam/config.py`. It uses python-hcl2's **parse tree** (not its dict output,
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
  sensitivity). The port originally ignored such markers; they are now walked like resource markers
  (see "Security hardening" below).
- `generate_resource_overview` mutates the plan in place, as Go does for outputs, so the plan Go
  serialized as `plan.js` shows the "Sensitive Value" placeholder. (`plan.js` is no longer written; see
  "Security hardening".)
- `StateResource.values` is now `None` when absent (Go nil map) so empty-vs-missing matches.

## Step 4b: map (`tfroam/map.py`)

- Faithful quirks kept: `line` and `required` are pointers in Go, so they're emitted even when `0`/`false`;
  `AddFileIfNotExists` builds `source` as `"<module source>/<file>"`, and module nodes have no source, so
  file nodes under a module get `"/main.tf"`; locals are collected only from resource and module-call
  expressions (not data sources, and not from nested blocks); outputs/variables fall back to the plan's
  configuration only when the module's `.tf` files could not be loaded.
- New fixture `noconfig-test`: a working dir whose `main.tf` has a syntax error, so rover logs "Could not
  load configuration" and puts everything under "unknown file". (Go refuses a working dir that doesn't
  exist at all: "error initialising Terraform with workdir".)

## Step 4c: graph (`tfroam/graph.py`)

- Go iterates maps in random order, so node/edge **order** in its `graph.js` differs from run to run
  (content does not). The port iterates sorted keys, so its output is reproducible; tests compare
  nodes and edges by id. The frontend only reads the lists as sets.
- Kept as-is: edges are made only for whole-resource references (exactly two dotted parts) and
  data-source references (three parts); `each.*` references are skipped; `var.`/`output.`/`local.`/
  `module.`/`data.` colours follow the substring order in `addEdges`; a `terraform.workspace` reference
  adds a synthetic `locals` node parented to the base path.
- Go would panic on an output whose configuration has no expression; the port skips it.

## Step 5: server, standalone zip, CLI

- **Frontend:** `tfroam/ui/dist` is upstream's committed `ui/dist` at the pinned commit, verbatim
  (MIT; see `LICENSE.rover`). A fresh `npm ci && npm run build` of the upstream source in this sandbox
  produced the same code, same file sizes and identical vendor/CSS bundles; only `app.js` got a different
  content hash (it only changes the `sourceMappingURL` comment). `scripts/build_ui.sh` redoes the build.
- **Zip:** every frontend file in the generated zip is byte-identical to the Go zip's (file order too),
  including the `index.html` rewrite. Go's `strings.Split(html, "</head>")` drops the closing `</head>`
  tag and the three injected script tags are separated by `\n\t\t`; both are reproduced. `plan.js` is
  written but `index.html` does not load it (as in Go); it has since been dropped (see "Security hardening"). The data files are JSON-equivalent to Go's;
  `graph.js` list order differs (see graph notes).
- **Server:** stdlib `ThreadingHTTPServer`, no new dependency. `/health`, `/api/{plan,rso,map,graph}`
  with `Access-Control-Allow-Origin: *`, an unknown `/api/x` answers 200 with Go's help text. (`/api/plan`
  and the CORS header have since been removed; see "Security hardening".) The Go
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

## Step 6: image mode (`tfroam/screenshot.py`)

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
  virtualenv outside the source tree: the wheel carries the UI (`tfroam/ui/dist`, 20 files), `py.typed`, the
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
- **Names:** the distribution, the import package and a console command are all `tfroam` (`rover` was
  taken on PyPI; `tfroam` was free when checked but nothing is published yet, so re-check before the
  first upload). The `rover` console command is kept as a second entry point for the same program, so the
  README, the Docker entrypoint and existing scripts are unchanged. The original spec named the import
  package `rover`; it was renamed so installing `tfroam` cannot collide with another distribution that
  provides a top-level `rover` module.
- Repo hygiene: `.gitignore` anchors `/dist/` and `/build/` so it cannot hide `tfroam/ui/dist`.

## Addition beyond the port: `-hide`

- `-hide locals,variables,outputs` (comma-separated or repeated; the Go version has no such flag) removes those
  node kinds from the **graph** document, every edge touching them, and any file box left empty. The map and
  rso documents are untouched. Default is off, so all golden comparisons still hold; the filter is applied
  after the (Go-identical) graph is built, in `tfroam/graph.py:filter_graph`.
- Motivation: on a real 61-resource, 3-module AWS plan, `local.name` and variable references account for
  roughly half of the 153 edges, which makes the picture hard to read. The frontend is unchanged.
- An unknown value exits 1 with the list of valid ones.

## Security hardening (beyond the port)

These are deliberate divergences from the Go version. Before them, only sensitive *outputs* were masked:
a resource attribute marked sensitive (for example a database `password`) was copied into `rso.js`,
`plan.js` and `GET /api/rso`, and the API answered with `Access-Control-Allow-Origin: *`.

- **Redaction (`tfroam/redact.py`).** Unless `-showSensitive`, `generate_resource_overview` calls
  `redact_plan` before building anything, and `redact_module` on every `.tf` module it loaded. Every
  redacted value becomes `"Sensitive Value"`, the placeholder Go used for outputs. Rules
  (`redact_value(value, marker)`):
  - `true` marks the whole value (or subtree); `false`, `null`, `{}` and `[]` mark nothing.
  - An object marker on an object recurses per key; keys the marker does not name are kept, and marker
    keys missing from the value add nothing. A list marker on a list recurses per index; elements past
    the end of the marker are kept.
  - A marker whose shape doesn't match the value (object marker on a string, list marker on an object, a
    truthy non-bool scalar) replaces the whole value if it marks anything.
  - Applied to: `resource_changes[].change.before/after` against `before_sensitive`/`after_sensitive`;
    `output_changes` the same way (object-shaped output markers, on which Go panics, now redact the
    marked attributes); `prior_state` and `planned_values` resource `values` against `sensitive_values`
    in every module (a whole-value `true` keeps the keys and masks every value, because rso skips
    resources whose `values` are absent); state outputs flagged `sensitive`; the `default` of variables
    declared `sensitive = true`, both in the plan `configuration` and in the `.tf` data from
    `tfroam/config.py`; literal module-call arguments bound to a sensitive child variable; plan
    `variables` values of sensitive root variables.
  - The markers themselves are kept, so the UI can still tell which attributes are sensitive.
  - `config.Variable` gained a `sensitive` field (parsed from the block, never serialized:
    terraform-config-inspect has no such field, so the `map`/`rso` JSON shape is unchanged). A
    non-constant `sensitive` expression counts as sensitive.
  - The plan models already kept `sensitive_values` and the change `*_sensitive` fields, so nothing had to
    be retained specially. None of the Go goldens has sensitive resource data, so every golden
    comparison is unchanged; `tests/fixtures/sensitive-test/plan.json` is hand-written (not from Go).
- **No published plan.** The bundled UI only fetches `/api/rso`, `/api/map` and `/api/graph`, and the
  standalone `index.html` never loaded `plan.js`. `/api/plan` is removed (it now gets the help text,
  which lists `rso, map, graph`), and the zip no longer contains `plan.js`. `Assets.plan` stays for
  internal use; `Assets.documents()` returns only the three published documents. Tests that compared
  the zip's `plan.js` or `/api/plan` with the Go golden now assert they are absent instead; the plan
  goldens are still used as test inputs and by `tests/test_plan.py`/`test_rso.py`.
- **No CORS header.** `Access-Control-Allow-Origin: *` is gone; the UI is served from the same origin.
- **DNS-rebinding guard.** When `-ipPort` binds a loopback address (127.0.0.0/8, `::1` or `localhost`),
  requests whose `Host` hostname is not `localhost`, `127.0.0.1`, `::1` (`[::1]`) or the bound host get
  `403`; so do HTTP/1.1 requests without a `Host` header and requests with more than one. A wildcard or
  non-loopback binding (such as `0.0.0.0:9000`, which the Docker image uses) is not filtered, because the
  names it is reached under are unknown. `-genImage` points the browser at `127.0.0.1` or the bound
  host, which are always allowed.

## CLI and Terraform runner changes (beyond the port)

These deliberately differ from the Go version. They supersede the `init` flags and "captured, not
streamed" in "Step 3" and the image-mode address and default `-ipPort` in "Step 6".

- **Bind address:** `-ipPort` defaults to `127.0.0.1:9000` (Go: `0.0.0.0:9000`), so the plan view is not
  exposed to the network by default. The Docker image passes `-ipPort 0.0.0.0:9000` explicitly; a repeated
  flag overrides an earlier one (last one wins, as with Go's `flag`), so `docker run ... -ipPort X` still
  works.
- **`init`** is now `init -no-color -input=false -backend=true -get=true -upgrade=false
  [-backend-config=...]`:
  - `-upgrade=false` by default (terraform-exec passes `-upgrade=true`, which rewrites
    `.terraform.lock.hcl` and re-fetches modules on every run). `-tfUpgrade` restores `-upgrade=true`.
  - No `-force-copy`. With it, a changed backend configuration made `init` copy state to the new backend
    without asking. Without it (and with `-input=false`), Terraform fails with "Backend configuration
    changed" and tells the user to run `init -migrate-state` or `-reconfigure`; Rover reports that as
    "Unable to initialize Terraform Plan: ...". Checked with Terraform 1.15.8 using a local backend whose
    `path` changed: init exits 1 and no state file is created at the new path
    (`test_real_terraform_refuses_backend_change_instead_of_migrating`).
- **`plan`** is `plan -no-color -input=false -detailed-exitcode -out=<tmp> [-var-file=...]
  -lock=<tfLock> -parallelism=10 -refresh=<tfRefresh> [-var k=v ...]`. `-tfLock` and `-tfRefresh` default
  to `true` (same as Go and terraform-exec); `-tfLock=false` / `-tfLock false` turn them off. `show -json`
  is unchanged.
- **`-verbose`** streams the stdout and stderr of `init`, `workspace select` and `plan` to Rover's stderr
  line by line as they run (two reader threads over `Popen` pipes, argument list, no shell). Both streams
  are still captured, so a failure's error message still carries Terraform's stderr. `show -json` is never
  streamed: its stdout is the plan. Without `-verbose`, commands run through `subprocess.run` as before.
- **Validation before Terraform runs:** the Terraform Cloud flag error, unknown `-hide` values
  (`graph.validate_hide`, which `filter_graph` also uses) and a missing Playwright for `-genImage` are all
  checked in `cli.run` before `generate_assets`, so they fail in well under a second instead of after
  `init` and `plan`. `-genImage` together with `-standalone` still does not need Playwright, because
  standalone mode exits before image mode (unchanged behaviour).
- **Image mode address:** with `-genImage` and no explicit `-ipPort`, the server binds `127.0.0.1:0` and the
  browser is pointed at `RoverServer.port`, the port actually bound, so image mode works while another Rover
  holds port 9000. An explicit `-ipPort` is used as before. `server.start_server` still logs the requested
  address (`Rover is running on 127.0.0.1:0`), so the CLI logs the bound address after it.
- **`-imagePath`** (default `rover.svg`, relative to the current directory like Go's `./rover.svg`) is passed
  to `screenshot.screenshot(output=...)`. An empty value is rejected before Terraform runs.
- **Usage text:** `-tfPath` shows `(default "terraform" on PATH, else "/bin/terraform")`, which is what
  `resolve_tf_path` does. Bool flags whose default is `true` print `(default true)` as Go's `flag` does;
  `false` defaults are not printed. A test checks every `FLAGS` default against `Options`.
- **Real-Terraform test:** `test_real_terraform_simple_fixture` now also ignores the
  `prior_state.terraform_version` field. The goldens were captured with 1.9.8, so any other Terraform
  version failed that test before this change.
