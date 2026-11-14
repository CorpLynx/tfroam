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

- **Frontend (superseded, see "UI changes from upstream"):** `tfroam/ui/dist` is upstream's committed `ui/dist` at the pinned commit, verbatim
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
  holds port 9000. An explicit `-ipPort` is used as before. `server.start_server` logs the address actually
  bound (`Rover is running on 127.0.0.1:<port>`), not the requested `:0`.
- **`-imagePath`** (default `rover.svg`, relative to the current directory like Go's `./rover.svg`) is passed
  to `screenshot.screenshot(output=...)`. An empty value is rejected before Terraform runs.
- **Usage text:** `-tfPath` shows `(default "terraform" on PATH, else "/bin/terraform")`, which is what
  `resolve_tf_path` does. Bool flags whose default is `true` print `(default true)` as Go's `flag` does;
  `false` defaults are not printed. A test checks every `FLAGS` default against `Options`.
- **Real-Terraform test:** `test_real_terraform_simple_fixture` now also ignores the
  `prior_state.terraform_version` field. The goldens were captured with 1.9.8, so any other Terraform
  version failed that test before this change.

## Docker, CI and packaging changes
- **Entrypoint:** `ENTRYPOINT ["rover", "-ipPort", "0.0.0.0:9000"]`. The native `-ipPort` default is now
  `127.0.0.1:9000`, which would make `docker run -p 9000:9000` unreachable; the entrypoint restores the
  all-interfaces bind inside the container. Flags are applied in order, so a user's own `-ipPort` (appended
  after the entrypoint) still wins. `-genImage` in the container therefore serves on 0.0.0.0:9000 internally,
  as before.
- **`TF_VERSION=1.5.5`** is kept: it is upstream's default at the pinned commit (`ARG TF_VERSION=1.5.5`) and
  the last MPL-2.0 Terraform release (1.6+ is BUSL-1.1). Goldens and CI use 1.9.8; pass
  `--build-arg TF_VERSION=...` for a newer one.
- **Base image:** both Python stages use `python:3.12-slim@sha256:5e2bdb1b...` (the multi-arch OCI index
  digest, read from the registry's `Docker-Content-Digest` header and checked against the body's SHA-256).
  The `PYTHON_VERSION` build arg was removed because overriding the tag of a digest-pinned reference would be
  silently ignored. `hashicorp/terraform:${TF_VERSION}` stays tag-based so the build arg keeps working.
- **User:** the image still runs as root, as upstream does, so the bind-mounted `/src` stays writable for
  `rover.zip`, `rover.svg` and `.terraform`.
- **CI:** every action is pinned to a commit SHA with the version in a comment. The test matrix is Python
  3.11 and 3.14 (single-version jobs stay on 3.12). The Docker job now starts the server detached with
  `-p 9000:9000`, waits for `/health`, checks `/api/graph` is JSON and removes the container even on
  failure. A new OpenTofu **smoke** job runs `rover -tfPath tofu -standalone true` on `simple-test` (one
  output, no providers, so `tofu init` needs no downloads) and checks the zip has `index.html`.
- **Dependabot:** weekly `pip`, `github-actions` and `docker` updates, minor and patch grouped per ecosystem.
- **The image has still not been built locally (no Docker on the development machine);** the CI `docker`
  job is the only build and run check.

## UI changes from upstream

The frontend is no longer upstream's bundle. Reason: on a real plan (223 resources, 4 accounts, 10 modules)
the upstream page was hard to use. Measured in headless Chrome at 1600x1000 before changing anything:

- moving the pointer across the graph put a dimming class on 953 elements for every node it crossed (flicker);
- one mouse-wheel notch zoomed 2.5x (four notches: 40x), and the page could not be scrolled over the graph;
- nodes were draggable and the whole graph sits inside one compound node, so a drag meant to pan moved a box;
- the graph got 907x1000 px of a 11,186 px tall page; at fit, labels were 1.2 px high (0.5 px at 1100 px wide);
- the details box started 824 px down the left column, under a legend that took a third of the width;
- the resource list was below the graph; there was no search, and only 2 focusable elements on the page.

**Build.** Upstream's `ui/` source at commit `6257a19a` is vendored in `ui/` (MIT, `ui/LICENSE`) and edited
there; `scripts/build_ui.sh` runs `npm ci && npm run build` in it and copies `ui/dist` to `tfroam/ui/dist`.
`package.json` and `package-lock.json` are upstream's, byte for byte, so the toolchain and every library
version are the ones upstream's own bundle was built with (cytoscape 3.20.0, Vue 2.6.14). No dependency was
added. `vue.config.js` is new: it turns source maps off (they were 4 MB of the wheel). `chota.min.css`, the
provider icons and `cytoscape-node-html-label` are no longer used; the dist went from 20 files to 5.

**Kept, because the Python side or the goldens rely on it** (pinned by `tests/test_ui_bundle.py`):

- `#cytoscape-div` and `#saveGraph` (`tfroam/screenshot.py`). `#saveGraph` is now a `<button>`. A click that
  arrives before the plan has loaded is remembered and runs when the graph is ready.
- **The saved SVG is unchanged.** Upstream's cytoscape stylesheet is kept verbatim in
  `ui/src/components/Graph/upstreamStyle.js`. The layout (klay, same options, same element order) is computed
  with it, and "Save SVG" switches back to it for the export, so `test_real_browser_svg_matches_go_reference`
  still passes against the Go reference. The on-screen sheet (`screenStyle.js`) only adds paint: colours,
  opacity, interaction states. Two things to know if you edit it: node sizes are in `em` of `#cytoscape-div`,
  whose font size must stay 16px; and anything that changes a node's size or label font changes the layout.
- `index.html` keeps `<div id="app">` and absolute `="/..."` asset paths (`export.standalone_index`). The
  bundle no longer contains images, so `export.standalone_js` has nothing to rewrite; it is left in place.
- The page reads only `rso`, `map` and `graph`, from globals in standalone mode and from `/api/*` otherwise
  (now loaded once in `ui/src/model.js` instead of once per component). The backend is untouched.
- Nothing is fetched from another host; fonts are system fonts.

**Changed behaviour.**

- Layout: top bar (list toggle, search, theme, Save SVG), resource list on the left, graph in the rest,
  details in a panel floating over the right of the graph (a bottom sheet under 700 px; under 900 px the list
  opens over the graph). The page itself never scrolls.
- Hover tints the node and its own lines and shows a tooltip with the address. Only a click selects.
  A click on the background, a file or a type box clears the selection (upstream: only the directory or a
  file did; a type box selected a nonexistent "resource").
- Nodes cannot be dragged (`autoungrabify`). Wheel zoom is handled by the page: about 1.2x per notch,
  proportional for trackpads, pinch supported. Zoom is limited to between 0.6x of "fit" and 2x.
- Level of detail: below 11 px on screen, box titles are drawn as HTML labels of fixed size (outer boxes win
  when two would overlap). The selected node gets an HTML ring of at least 18 px, and highlighted lines and
  search outlines are re-sized after zooming so they stay about 2.5 px wide.
- Selecting an instance (`x["a"]`, `x[0]`) highlights the lines of its block, which is where the graph
  attaches them. A block's own instances are not listed as its connections.
- Search over the addresses in `map` (every term must match). Selection from the list or search moves the
  camera only if the node is off screen or too small to read.
- Details panel, same data as upstream with these corrections:
  - `false`, `0` and `""` are shown as such (upstream printed `null`, or the raw expression object);
  - only attributes that are wholly unknown read `known after apply` (upstream replaced every key present in
    `after_unknown`, including partly known maps such as `tags_all`);
  - an instance shows its block's configuration with a link to the block (upstream: "Please check parent
    resource"); a block lists its instances on the state tabs;
  - `expressions` are shown one argument per row, and references that resolve to an address in the plan
    are links; `address`, `mode`, `name`, `type` and `schema_version` rows are dropped (the header has them);
  - root outputs show their planned value: `rso.states` stores them under the bare name, which upstream's
    lookup (`output.<name>`) never found;
  - a new Connections tab lists "depends on" and "used by" from the graph's edges.
- Sensitive values: the page shows what the backend sends, so anything redacted reads `Sensitive Value`.
  It does not read `*_sensitive` markers and has no way to reveal a value.
- The title is `<directory> | tfroam` and the header no longer links to the upstream repository
  (no external links on the page).

**Not done / known limits.**

- Verified in headless Chrome only (served, `file://` from the zip, `-genImage`); not in Firefox or Safari.
- The graph itself has no keyboard navigation between nodes; the list and search are the keyboard route.
- The klay layout and upstream's box sizes are unchanged (the price of an unchanged SVG), so a large plan
  still fits the window only at a small scale; labels, search and "Show in graph" are how it is made readable.
- A plan with many hundreds of boxes produces as many HTML labels when zoomed out; they are culled to the
  viewport but not otherwise limited.
- `export.standalone_js` and the `.map` content type in `server.py` are now unused by the bundle.
