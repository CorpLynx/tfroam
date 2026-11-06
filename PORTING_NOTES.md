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
