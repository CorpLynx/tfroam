// On-screen graph stylesheet: upstream's sheet (geometry) followed by paint-only rules
// for the current theme and the interaction states. Rules here must not change node
// sizes, padding or label fonts, or the layout would no longer match the exported SVG.

import upstreamStyle from "./upstreamStyle";

// `lineWidth` is the width (in graph units) of highlighted lines; Graph.vue picks it from the
// zoom level so the lines of the selected node stay visible when the whole graph is on screen.
// `pixelRatio`: cytoscape measures min-zoomed-font-size in device pixels, the sizes below are CSS pixels.
export function screenStyle(p, lineWidth = 8, pixelRatio = 1) {
  const arrow = { "target-arrow-shape": "triangle", "arrow-scale": 1.5, opacity: 1, width: lineWidth, "z-index": 10 };
  const box = {
    "border-color": p.boxLine,
    "background-color": p.surface,
    "text-background-color": p.surface,
    color: p.ink,
  };
  return upstreamStyle.concat([
    // Labels smaller than this on screen are skipped; HTML labels take over for boxes (see Graph.vue).
    { selector: "node", style: { color: p.ink, "min-zoomed-font-size": 9 * pixelRatio, "overlay-opacity": 0 } },
    {
      selector: "edge",
      style: { "line-fill": "solid", "line-color": p.edge, width: 6, opacity: 0.75, "overlay-opacity": 0 },
    },
    // The working directory is the canvas itself: no box, no (absolute path) label.
    { selector: ".basename", style: { "background-opacity": 0, "border-opacity": 0, "text-opacity": 0 } },
    { selector: ".fname", style: { "background-color": p.surface, "border-color": p.line, color: p.muted } },
    {
      selector: ".module",
      style: { color: p.accent, "border-color": p.accent, "background-color": p.accentSoft },
    },
    { selector: ".data-type", style: box },
    { selector: ".resource-type", style: box },
    { selector: ".resource-parent", style: box },
    { selector: ".data-name", style: { "background-color": p.dataSoft, "border-color": p.data, color: p.ink } },
    { selector: ".output", style: { "background-color": p.outputSoft, "border-color": p.output, color: p.ink } },
    {
      selector: ".variable",
      style: { "background-color": p.variableSoft, "border-color": p.variable, color: p.ink },
    },
    { selector: ".locals", style: { "background-color": p.local, "border-color": p.local, color: p.onLocal } },
    { selector: ".resource-name", style: { "background-color": p.none, color: "#ffffff" } },
    { selector: ".create", style: { "background-color": p.create, color: "#ffffff" } },
    { selector: ".delete", style: { "background-color": p.delete, color: "#ffffff" } },
    { selector: ".update", style: { "background-color": p.update, color: "#ffffff" } },
    { selector: ".replace", style: { "background-color": p.replace, color: p.onReplace } },
    { selector: ".no-op", style: { "background-color": p.surface, "border-color": p.boxLine, color: p.ink } },

    // Names of single resources are short and bold: keep drawing them a little smaller than box titles.
    {
      selector: ".resource-name, .data-name, .output, .variable, .locals",
      style: { "min-zoomed-font-size": 6.5 * pixelRatio },
    },
    // Set on boxes while their HTML label is in use (zoomed out), so the title is not drawn twice.
    { selector: "node.html-label", style: { "text-opacity": 0, "text-background-opacity": 0 } },

    // Interaction states. Order matters: later rules win.
    { selector: "node.faded", style: { opacity: 0.3 } },
    { selector: "edge.faded", style: { opacity: 0.08 } },
    // An outline, so a search match keeps its own colour; like the lines, it is sized for the zoom.
    {
      selector: "node.match",
      style: { "border-width": lineWidth * 1.5, "border-color": p.match, "border-opacity": 1, "border-style": "solid" },
    },
    { selector: "node.hover", style: { "overlay-color": p.accent, "overlay-opacity": 0.14, "overlay-padding": 10 } },
    {
      selector: "edge.hover-edge",
      style: { "line-color": p.muted, opacity: 1, width: Math.max(10, lineWidth * 0.7), "z-index": 5 },
    },
    {
      selector: "edge.e-out",
      style: Object.assign({ "line-color": p.accent, "target-arrow-color": p.accent }, arrow),
    },
    {
      selector: "edge.e-in",
      style: Object.assign(
        {
          "line-color": p.edgeIn,
          "target-arrow-color": p.edgeIn,
          "line-style": "dashed",
          "line-dash-pattern": [lineWidth * 2.5, lineWidth * 1.5],
        },
        arrow
      ),
    },
  ]);
}
