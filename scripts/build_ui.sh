#!/usr/bin/env bash
# Rebuild tfroam/ui/dist from the UI source in ui/.
# Only needed after changing ui/; end users never need Node.
#
# ui/ started as the upstream Rover frontend (github.com/im2nguyen/rover, ui/ at the commit
# below, MIT) and is maintained here. package.json and package-lock.json are upstream's, so
# `npm ci` installs exactly the toolchain upstream's bundle was built with.
#
#   scripts/build_ui.sh              clean install of the locked dependencies, then build
#   scripts/build_ui.sh --no-install reuse ui/node_modules (faster while iterating)
set -euo pipefail

UPSTREAM_COMMIT="6257a19a32c0116eae05c91e3c295d07c54c3a05"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
UI="$HERE/ui"

cd "$UI"
if [[ "${1:-}" != "--no-install" || ! -d node_modules ]]; then
  npm ci --no-audit --no-fund
fi
npm run build

rm -rf "$HERE/tfroam/ui/dist"
cp -R "$UI/dist" "$HERE/tfroam/ui/dist"
echo "Updated $HERE/tfroam/ui/dist from $UI (based on upstream $UPSTREAM_COMMIT)"
