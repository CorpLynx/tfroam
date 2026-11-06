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
