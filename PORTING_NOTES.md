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
