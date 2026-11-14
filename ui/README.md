# tfroam UI

The web page `rover` serves: a Vue 2 single-page app that draws the plan with cytoscape.

It started as the `ui/` directory of [im2nguyen/rover](https://github.com/im2nguyen/rover) at commit
`6257a19a32c0116eae05c91e3c295d07c54c3a05` (MIT, see `LICENSE` in this directory) and has been
reworked since; `PORTING_NOTES.md` in the repository root lists what changed and why.
`package.json` and `package-lock.json` are upstream's, unchanged.

The built files are committed in `tfroam/ui/dist`, so users of the Python package never need Node.
After changing anything here, rebuild them from the repository root:

```
$ scripts/build_ui.sh
```

Things the Python side relies on (see `tfroam/screenshot.py`, `tfroam/export.py`):

- the graph container has the id `cytoscape-div`, and the button with the id `saveGraph` downloads `rover.svg`;
- `index.html` contains `<div id="app">` and references its assets as `="/..."`;
- the page reads `rso`, `map` and `graph` from globals when they exist (standalone mode) and from
  `/api/rso`, `/api/map`, `/api/graph` otherwise;
- nothing is loaded from another host: no CDN, no web fonts, no analytics.
