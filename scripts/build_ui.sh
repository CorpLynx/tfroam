#!/usr/bin/env bash
# Rebuild tfroam/ui/dist from the upstream Rover frontend at the pinned commit.
# Only needed when updating the bundled UI; end users never need Node.
set -euo pipefail

UPSTREAM_COMMIT="6257a19a32c0116eae05c91e3c295d07c54c3a05"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

git clone --quiet https://github.com/im2nguyen/rover.git "$WORK/rover"
git -C "$WORK/rover" checkout --quiet "$UPSTREAM_COMMIT"
(cd "$WORK/rover/ui" && npm ci --no-audit --no-fund && npm run build)

rm -rf "$HERE/tfroam/ui/dist"
cp -r "$WORK/rover/ui/dist" "$HERE/tfroam/ui/dist"
echo "Updated $HERE/tfroam/ui/dist from $UPSTREAM_COMMIT"
