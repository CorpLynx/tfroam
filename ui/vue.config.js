// Build settings for the bundled UI (see scripts/build_ui.sh).
module.exports = {
  // The page is served by `tfroam` itself and, in -standalone mode, opened from disk:
  // tfroam/export.py rewrites the absolute "/..." asset paths to "./...".
  publicPath: "/",
  // Source maps would triple the wheel and are not needed: the source is in ui/.
  productionSourceMap: false,
};
