<template>
  <div
    class="graph"
    ref="wrap"
    tabindex="0"
    role="application"
    aria-label="Resource graph. Drag to pan, scroll to zoom. Arrow keys pan, plus and minus zoom, 0 fits the graph. Use the resource list or search to select a resource."
    @keydown="onKey"
    @wheel.capture="onWheel"
    @pointerdown.capture="onPointerDown"
    @mouseleave="hideTip"
  >
    <cytoscape ref="cy" :config="config" :preConfig="preConfig"></cytoscape>
    <div class="graph-labels" ref="labels" aria-hidden="true"></div>
    <div class="graph-marker" ref="marker" aria-hidden="true"></div>
    <div class="graph-tip" v-show="tip.show" :style="tipStyle" role="presentation">
      <span class="graph-tip-address">{{ tip.title }}</span>
      <span class="graph-tip-meta">{{ tip.meta }}</span>
    </div>
    <p class="graph-status" v-if="status">{{ status }}</p>
    <div class="graph-controls" role="group" aria-label="Zoom">
      <button type="button" title="Zoom in (+)" aria-label="Zoom in" @click="zoomBy(1.4)">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>
      </button>
      <button type="button" title="Zoom out (-)" aria-label="Zoom out" @click="zoomBy(1 / 1.4)">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h10" /></svg>
      </button>
      <button type="button" title="Fit the whole graph (0)" aria-label="Fit the whole graph" @click="fit()">
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10" />
        </svg>
      </button>
    </div>
  </div>
</template>

<script>
import { saveAs } from "file-saver";
import klay from "cytoscape-klay";
import svg from "cytoscape-svg";
import upstreamStyle from "./upstreamStyle";
import { screenStyle } from "./screenStyle";
import { prefersReducedMotion } from "@/theme";

const CONTAINER_TYPES = ["basename", "file"];
// Label size in model units (upstream: 2em of a 16px container).
const LABEL_PX = 32;
// Below this on-screen size the canvas label is unreadable and an HTML label is drawn instead.
const HTML_LABEL_BELOW = 11;
// Height of an HTML box label (11px text on a 15px line, 1px padding top and bottom).
const LABEL_H = 17;
const MAX_ZOOM = 2;
const KEY_HEIGHT = 40;
// "Show in graph" never zooms in further than this: labels are 16px here.
const READ_ZOOM = 0.5;

export default {
  name: "Graph",
  props: {
    graphData: { type: Object, default: null },
    selectedId: { type: String, default: "" },
    matches: { type: Array, default: () => [] },
    palette: { type: Object, required: true },
    // Width covered by the details panel, so "zoom to" centres in the visible part.
    rightInset: { type: Number, default: 0 },
    // Height it covers when it is a sheet along the bottom instead (narrow windows).
    bottomInset: { type: Number, default: 0 },
  },
  data() {
    return {
      // The upstream sheet is used for the layout; the themed sheet replaces it right after.
      config: {
        autounselectify: true,
        autoungrabify: true, // dragging always pans; nodes cannot be dragged out of place by accident
        boxSelectionEnabled: false,
        maxZoom: MAX_ZOOM,
        style: upstreamStyle,
      },
      tip: { show: false, x: 0, y: 0, title: "", meta: "" },
      status: "Laying out the graph…",
    };
  },
  computed: {
    tipStyle() {
      return { transform: `translate(${this.tip.x}px, ${this.tip.y}px)` };
    },
  },
  watch: {
    graphData() {
      this.renderGraph();
    },
    selectedId() {
      this.applySelection();
    },
    matches() {
      this.applyMatches();
    },
    palette() {
      const cy = this.cy();
      if (cy && this.rendered) this.applyScreenStyle();
    },
  },
  methods: {
    cy() {
      return this.$refs.cy && this.$refs.cy.instance;
    },
    preConfig(cytoscape) {
      cytoscape.use(klay);
      cytoscape.use(svg);
    },
    renderGraph() {
      const cy = this.cy();
      if (!cy || !this.graphData || !this.graphData.nodes) return;
      const nodesNames = new Set(this.graphData.nodes.map((x) => x.data.id));

      cy.remove(cy.elements());
      this.graphData.nodes.forEach((n) => cy.add(n));
      this.graphData.edges.forEach((n) => {
        if (n.data.id.includes("-variable") || n.data.id.includes("-output")) return;
        // Browse node ancestors if target is not found in nodes
        // e.g: resource.test.attribute will not be in nodes, as attributes are not exported
        // this ensures that the dependency will be made on resource.test instead
        let name = n.data.target;
        while (!nodesNames.has(name)) {
          name = name.split(".");
          if (name.length < 2) {
            console.warn("edge target", n.data.target, "not found in nodes");
            return;
          }
          name.pop();
          name = name.join(".");
        }
        n.data.target = name;
        cy.add(n);
      });

      this.runLayouts();
      this.applyScreenStyle();
      this.rendered = true;
      this.farLabels = null;
      this.status = cy.nodes().length ? "" : "This plan has nothing to draw.";

      // Boxes that get an HTML label when zoomed out, most important first.
      this.boxes = cy
        .nodes()
        .filter((n) => n.isParent() && n.data("type") !== "basename")
        .map((n) => {
          const type = n.data("type");
          return {
            id: n.id(),
            node: n,
            type,
            text: type === "module" ? `module.${n.data("label")}` : n.data("label"),
            rank: n.ancestors().length - (type === "module" ? 0.5 : 0),
          };
        })
        .sort((a, b) => a.rank - b.rank);
      this.resetView();
      this.bindEvents(cy);
      this.applySelection();
      this.applyMatches();
      this.$emit("ready");
      if (this.saveWhenReady) {
        this.saveWhenReady = false;
        this.saveGraph();
      }
    },
    // Highlighted lines are drawn about 2.5px wide on screen, whatever the zoom.
    lineWidth() {
      const w = 2.5 / this.cy().zoom();
      return Math.max(8, Math.min(80, Math.round(w / 4) * 4));
    },
    applyScreenStyle() {
      this.styledWidth = this.lineWidth();
      this.cy().style(screenStyle(this.palette, this.styledWidth, window.devicePixelRatio || 1));
    },
    // After zooming with something selected or found, restyle once the zoom has settled.
    scheduleLineWidth() {
      if (!this.selectedId && !this.matches.length) return;
      window.clearTimeout(this.lineTimer);
      this.lineTimer = window.setTimeout(() => {
        if (this.rendered && this.lineWidth() !== this.styledWidth) this.applyScreenStyle();
      }, 140);
    },
    runLayouts() {
      this.cy()
        .layout({
          name: "klay",
          nodeDimensionsIncludeLabels: true,
          klay: {
            direction: "RIGHT",
            borderSpacing: 100,
            spacing: 30,
          },
        })
        .run();
    },
    bindEvents(cy) {
      if (this.bound) return;
      this.bound = true;
      let lastTap = { id: "", at: 0 };

      cy.on("tap", (event) => {
        const n = event.target;
        if (n === cy || !n.isNode || !n.isNode()) {
          this.$emit("select", "");
          return;
        }
        this.hideTip();
        const now = Date.now();
        const isDouble = lastTap.id === n.id() && now - lastTap.at < 350;
        lastTap = { id: n.id(), at: now };
        if (isDouble) {
          this.zoomTo(n.id());
          return;
        }
        // Files, the directory and the boxes that only group a resource type have no details.
        this.$emit("select", this.isSelectable(n) ? n.id() : "");
      });

      // Hover only tints the node and its own lines: nothing else changes, so crossing the graph is quiet.
      cy.on("mouseover", "node", (event) => {
        const n = event.target;
        if (n.data("type") === "basename") return;
        this.clearHover();
        this.hovered = n.union(this.edgeOwner(n).connectedEdges());
        if (n.data("type") !== "file") n.addClass("hover"); // tinting a whole file is noise
        this.hovered.edges().addClass("hover-edge");
        this.showTip(n, event.renderedPosition);
      });
      cy.on("mousemove", "node", (event) => {
        if (this.tip.show) this.placeTip(event.renderedPosition);
      });
      cy.on("mouseout", "node", () => {
        this.clearHover();
        this.hideTip();
      });

      cy.on("viewport", this.scheduleOverlay);
      cy.on("zoom", this.scheduleLineWidth);
      cy.on("resize", this.scheduleOverlay);
    },
    isSelectable(n) {
      const type = n.data("type");
      if (CONTAINER_TYPES.includes(type)) return false;
      return !n.id().includes(" {"); // "aws_vpc {main.tf}": a box grouping one resource type
    },
    // A for_each / count instance has no lines of its own: its parent block carries them.
    edgeOwner(n) {
      if (!n.connectedEdges().empty()) return n;
      const parent = n.parent();
      if (parent.nonempty() && this.isSelectable(parent) && !parent.connectedEdges().empty()) return parent;
      return n;
    },
    clearHover() {
      if (this.hovered) {
        this.hovered.removeClass("hover hover-edge");
        this.hovered = null;
      }
    },
    showTip(n, pos) {
      const id = n.id();
      const type = n.data("type");
      let title = id;
      let meta = type;
      if (id.includes(" {")) {
        title = n.data("label");
        meta = `${type} type in ${id.slice(id.indexOf("{") + 1, -1)}`;
      } else if (type === "file") {
        title = n.data("label");
        meta = "file, double-click to zoom in";
      } else if (n.data("change")) {
        meta = `${type}, ${n.data("change")}`;
      }
      this.tip.title = title;
      this.tip.meta = meta;
      this.tip.show = true;
      this.placeTip(pos);
    },
    placeTip(pos) {
      const wrap = this.$refs.wrap;
      const maxX = Math.max(8, wrap.clientWidth - 340);
      this.tip.x = Math.min(pos.x + 14, maxX);
      this.tip.y = pos.y + 18 > wrap.clientHeight - 56 ? pos.y - 52 : pos.y + 18;
    },
    hideTip() {
      this.tip.show = false;
    },

    // ---- selection and search highlighting -------------------------------------------------
    applySelection() {
      const cy = this.cy();
      if (!cy || !this.rendered) return;
      let links = { inGraph: false, dependsOn: [], usedBy: [] };
      cy.batch(() => {
        cy.elements().removeClass("sel nbr faded e-out e-in");
        const n = this.selectedId ? cy.getElementById(this.selectedId) : cy.collection();
        if (n.empty()) return;
        const owner = this.edgeOwner(n);
        const out = owner.outgoers();
        const inc = owner.incomers();
        const near = owner.union(n).union(out.nodes()).union(inc.nodes());
        const keep = near.union(near.descendants()).union(near.ancestors());
        cy.nodes()
          .difference(keep)
          .filter((e) => !CONTAINER_TYPES.includes(e.data("type")))
          .addClass("faded");
        cy.edges().difference(owner.connectedEdges()).addClass("faded");
        out.edges().addClass("e-out");
        inc.edges().addClass("e-in");
        near.addClass("nbr");
        n.addClass("sel");
        // A block and its own instances are one thing, not a dependency.
        const family = owner.descendants().union(owner.ancestors());
        links = {
          inGraph: true,
          dependsOn: out.nodes().difference(family).map((e) => e.id()),
          usedBy: inc.nodes().difference(family).map((e) => e.id()),
        };
      });
      this.$emit("links", links);
      if (this.selectedId && this.lineWidth() !== this.styledWidth) this.applyScreenStyle();
      this.scheduleOverlay();
    },
    applyMatches() {
      const cy = this.cy();
      if (!cy || !this.rendered) return;
      cy.batch(() => {
        cy.nodes(".match").removeClass("match");
        this.matches.forEach((id) => {
          const n = cy.getElementById(id);
          if (n.empty()) return;
          // Mark the instances rather than the box around them, so the mark is not one big wash.
          (n.isParent() && n.data("type") !== "module" ? n.children() : n).addClass("match");
        });
      });
      if (this.matches.length && this.lineWidth() !== this.styledWidth) this.applyScreenStyle();
    },
    hasNode(id) {
      const cy = this.cy();
      return !!cy && !!id && cy.getElementById(id).nonempty();
    },

    // ---- camera ------------------------------------------------------------------------------
    viewSize() {
      const cy = this.cy();
      const inset = this.rightInset < cy.width() - 240 ? this.rightInset : 0;
      const sheet = this.bottomInset < cy.height() - 160 ? this.bottomInset : 0;
      return { w: cy.width() - inset, h: cy.height() - sheet };
    },
    moveCamera(zoom, pan, animate) {
      const cy = this.cy();
      cy.stop();
      if (animate && !prefersReducedMotion()) {
        cy.animate({ zoom, pan }, { duration: 220, easing: "ease-out-cubic" });
      } else {
        cy.viewport({ zoom, pan });
      }
    },
    frame(bb, maxZoom, animate) {
      const cy = this.cy();
      const view = this.viewSize();
      const pad = 28;
      const w = view.w;
      const h = view.h - (this.bottomInset ? 0 : KEY_HEIGHT); // keep clear of the key along the bottom edge
      const fitZoom = Math.min((w - 2 * pad) / bb.w, (h - 2 * pad) / bb.h);
      const zoom = Math.max(cy.minZoom(), Math.min(fitZoom, maxZoom));
      const pan = {
        x: w / 2 - zoom * (bb.x1 + bb.w / 2),
        y: h / 2 - zoom * (bb.y1 + bb.h / 2),
      };
      this.moveCamera(zoom, pan, animate);
    },
    resetView() {
      const cy = this.cy();
      if (!cy || cy.nodes().empty()) return;
      cy.resize();
      cy.minZoom(1e-50);
      this.frame(cy.elements().boundingBox(), MAX_ZOOM, false);
      // Zooming out further than "a bit smaller than the whole graph" only loses the graph.
      cy.minZoom(cy.zoom() * 0.6);
      this.scheduleOverlay();
    },
    fit(animate = true) {
      const cy = this.cy();
      if (!cy || cy.nodes().empty()) return;
      this.frame(cy.elements().boundingBox(), MAX_ZOOM, animate);
    },
    zoomBy(factor, at) {
      const cy = this.cy();
      if (!cy) return;
      const { w, h } = this.viewSize();
      cy.stop();
      cy.zoom({
        level: Math.max(cy.minZoom(), Math.min(cy.maxZoom(), cy.zoom() * factor)),
        renderedPosition: at || { x: w / 2, y: h / 2 },
      });
    },
    panBy(dx, dy) {
      const cy = this.cy();
      if (cy) cy.panBy({ x: dx, y: dy });
    },
    // Bring a node into view at a size where its label can be read.
    zoomTo(id) {
      const cy = this.cy();
      const n = cy && id ? cy.getElementById(id) : null;
      if (!n || n.empty()) return false;
      const bb = n.boundingBox({ includeLabels: false, includeOverlays: false });
      this.frame(bb, READ_ZOOM, true);
      return true;
    },
    // Used when the selection comes from the list or search: move only if the node is not readable where it is.
    reveal(id) {
      const cy = this.cy();
      const n = cy && id ? cy.getElementById(id) : null;
      if (!n || n.empty()) return false;
      const bb = n.renderedBoundingBox({ includeLabels: false, includeOverlays: false });
      const { w, h } = this.viewSize();
      const visible = bb.x1 >= 0 && bb.y1 >= 0 && bb.x2 <= w && bb.y2 <= h;
      const readable = cy.zoom() * LABEL_PX >= HTML_LABEL_BELOW;
      if (!visible || !readable) this.zoomTo(id);
      return true;
    },
    // The details panel opens over the right of the graph: slide the selected node out from under it.
    keepClear(id) {
      const cy = this.cy();
      const n = cy && id ? cy.getElementById(id) : null;
      if (!n || n.empty()) return;
      const bb = n.renderedBoundingBox({ includeLabels: false, includeOverlays: false });
      const { w, h } = this.viewSize();
      const overX = w < cy.width() ? Math.max(0, Math.min(bb.x2, bb.x1 + 200) + 24 - w) : 0;
      const overY = h < cy.height() ? Math.max(0, Math.min(bb.y2, bb.y1 + 80) + 24 - h) : 0;
      if (overX || overY) {
        const pan = { x: cy.pan().x - overX, y: cy.pan().y - overY };
        this.moveCamera(cy.zoom(), pan, true);
      }
    },
    onWheel(e) {
      const cy = this.cy();
      if (!cy || !this.rendered) return;
      // Cytoscape's own wheel zoom is 2.5x per mouse notch; this one is about 1.2x and
      // proportional for trackpads. Pinch arrives as ctrl+wheel with small deltas.
      e.preventDefault();
      e.stopPropagation();
      let dy = e.deltaY;
      if (e.deltaMode === 1) dy *= 33;
      else if (e.deltaMode === 2) dy *= 400;
      const pinch = e.ctrlKey || e.metaKey;
      const step = Math.max(-60, Math.min(60, dy)) * (pinch ? 0.012 : 0.003);
      const rect = this.$refs.wrap.getBoundingClientRect();
      this.zoomBy(Math.exp(-step), { x: e.clientX - rect.left, y: e.clientY - rect.top });
    },
    // Mouse and pen drags pan here, not in Cytoscape. vue-cytoscape bundles its own Cytoscape
    // (3.10), which refuses to pan a drag that starts inside a compound node, and nearly the
    // whole graph sits inside the directory, file and module boxes. Touch keeps Cytoscape's own
    // pan and pinch. A press that moves less than 4px stays a click (Cytoscape's tap threshold),
    // and Cytoscape itself suppresses the tap once a drag passes that threshold.
    onPointerDown(e) {
      const cy = this.cy();
      if (!cy || !this.rendered) return;
      const touch = e.pointerType === "touch";
      cy.userPanningEnabled(touch);
      if (touch || e.button !== 0 || this.drag) return;
      if (e.target.closest && e.target.closest(".graph-controls")) return;
      this.drag = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: false };
      window.addEventListener("pointermove", this.onPointerMove, true);
      window.addEventListener("pointerup", this.onPointerUp, true);
      window.addEventListener("pointercancel", this.onPointerUp, true);
    },
    onPointerMove(e) {
      const d = this.drag;
      if (!d || e.pointerId !== d.id) return;
      const dx = e.clientX - d.x;
      const dy = e.clientY - d.y;
      if (!d.moved) {
        if (dx * dx + dy * dy < 16) return;
        d.moved = true;
        this.$refs.wrap.classList.add("is-panning");
        this.clearHover();
        this.hideTip();
        this.cy().stop(); // a running "zoom to" animation would fight the drag
      }
      d.x = e.clientX;
      d.y = e.clientY;
      this.cy().panBy({ x: dx, y: dy });
    },
    onPointerUp(e) {
      const d = this.drag;
      if (!d || e.pointerId !== d.id) return;
      this.endDrag();
    },
    endDrag() {
      this.drag = null;
      window.removeEventListener("pointermove", this.onPointerMove, true);
      window.removeEventListener("pointerup", this.onPointerUp, true);
      window.removeEventListener("pointercancel", this.onPointerUp, true);
      if (this.$refs.wrap) this.$refs.wrap.classList.remove("is-panning");
    },
    onKey(e) {
      if (e.target !== this.$refs.wrap || e.ctrlKey || e.metaKey || e.altKey) return;
      const step = e.shiftKey ? 240 : 80;
      const actions = {
        ArrowLeft: () => this.panBy(step, 0),
        ArrowRight: () => this.panBy(-step, 0),
        ArrowUp: () => this.panBy(0, step),
        ArrowDown: () => this.panBy(0, -step),
        "+": () => this.zoomBy(1.4),
        "=": () => this.zoomBy(1.4),
        "-": () => this.zoomBy(1 / 1.4),
        _: () => this.zoomBy(1 / 1.4),
        0: () => this.fit(),
      };
      if (actions[e.key]) {
        e.preventDefault();
        actions[e.key]();
      }
    },

    // ---- HTML overlay: box labels when zoomed out, and the selection marker ------------------
    scheduleOverlay() {
      if (this.overlayFrame) return;
      this.overlayFrame = window.requestAnimationFrame(() => {
        this.overlayFrame = 0;
        this.drawOverlay();
      });
    },
    drawOverlay() {
      const cy = this.cy();
      const layer = this.$refs.labels;
      if (!cy || !layer || !this.rendered) return;
      const pool = this.labelPool || (this.labelPool = new Map());
      const used = new Set();
      const far = cy.zoom() * LABEL_PX < HTML_LABEL_BELOW;
      const w = cy.width();
      const h = cy.height();
      const ring = this.markerRect(cy);

      if (far !== this.farLabels && this.boxes) {
        this.farLabels = far;
        cy.batch(() => this.boxes.forEach((box) => box.node.toggleClass("html-label", far)));
      }
      if (far && this.boxes) {
        // Outer boxes first: when two labels would overlap, the more important one stays
        // and the other appears once zooming in has pulled them apart.
        const placed = [];
        this.boxes.forEach((box) => {
          const bb = box.node.renderedBoundingBox({ includeLabels: false, includeOverlays: false });
          if (bb.w < 56 || bb.h < 18 || bb.x2 < 0 || bb.y2 < 0 || bb.x1 > w || bb.y1 > h) return;
          const isModule = box.type === "module";
          const maxWidth = Math.round(bb.w - (isModule ? 12 : 0));
          const textWidth = (t) => Math.ceil(t.length * 6.7) + 12;
          // "module.vpc_dev" when there is room, "vpc_dev" in a narrow box.
          const text = isModule && textWidth(box.text) > maxWidth ? box.node.data("label") : box.text;
          let width = Math.min(maxWidth, textWidth(text));
          // A module's name sits on its top border, to the right; other labels in the top left corner.
          let y = Math.round(isModule ? bb.y1 - 8 : bb.y1);
          let x = Math.round(isModule ? bb.x2 - width - 6 : bb.x1);
          // The selection ring is padded to stay visible when zoomed out, so it can reach over the title
          // of the box the selected node sits in: lift that title to sit just above the ring.
          if (ring && x < ring.x2 && x + width > ring.x1 && y < ring.y2 && y + LABEL_H > ring.y1) {
            y = Math.round(ring.y1 - LABEL_H - 1);
          }
          const hits = (r) => x < r.x2 && x + width > r.x1 && y < r.y2 && y + LABEL_H > r.y1;
          let blockers = placed.filter(hits);
          if (blockers.length && isModule) {
            // Give way to the label on the left by shortening, if enough of the name stays readable.
            const from = Math.max.apply(null, blockers.map((r) => r.x2)) + 3;
            width -= from - x;
            x = from;
            blockers = width >= 56 ? placed.filter(hits) : blockers;
          }
          if (blockers.length) return;
          placed.push({ x1: x, y1: y, x2: x + width, y2: y + LABEL_H });
          let el = pool.get(box.id);
          if (!el) {
            el = document.createElement("span");
            el.className = `graph-label graph-label-${box.type}`;
            pool.set(box.id, el);
          }
          if (el.textContent !== text) el.textContent = text;
          if (!el.parentNode) layer.appendChild(el);
          el.style.transform = `translate(${x}px, ${y}px)`;
          el.style.maxWidth = `${width}px`;
          used.add(box.id);
        });
      }
      pool.forEach((el, id) => {
        if (!used.has(id) && el.parentNode) layer.removeChild(el);
      });

      // A selected node is a few pixels wide when zoomed out: ring it at a size that can be seen.
      const marker = this.$refs.marker;
      if (!ring) {
        marker.style.display = "none";
        return;
      }
      marker.style.display = "block";
      marker.style.width = `${ring.w}px`;
      marker.style.height = `${ring.h}px`;
      marker.style.transform = `translate(${ring.left}px, ${ring.top}px)`;
    },
    // Where the selection ring goes, on screen: the node plus 4px all round, at least 18px square.
    // x1..y2 add the ring's 3px halo, for keeping labels clear of it.
    markerRect(cy) {
      const sel = this.selectedId ? cy.getElementById(this.selectedId) : null;
      if (!sel || sel.empty()) return null;
      const bb = sel.renderedBoundingBox({ includeLabels: false, includeOverlays: false });
      const w = Math.round(Math.max(bb.w + 8, 18));
      const h = Math.round(Math.max(bb.h + 8, 18));
      const left = Math.round(bb.x1 + bb.w / 2 - w / 2);
      const top = Math.round(bb.y1 + bb.h / 2 - h / 2);
      return { left, top, w, h, x1: left - 3, y1: top - 3, x2: left + w + 3, y2: top + h + 3 };
    },

    // ---- export ------------------------------------------------------------------------------
    // "Save SVG": the same image upstream Rover saves (its stylesheet, light, no highlighting),
    // whatever the theme, zoom or selection on screen.
    saveGraph() {
      const cy = this.cy();
      if (!cy || !this.rendered) {
        this.saveWhenReady = true; // clicked before the plan arrived (-genImage can be that quick)
        return;
      }
      cy.style(upstreamStyle); // the state classes used on screen have no rules in this sheet
      let svgContent;
      try {
        svgContent = cy.svg({ scale: 0.1, full: true });
      } finally {
        this.applyScreenStyle();
      }
      const blob = new Blob([svgContent], { type: "image/svg+xml;charset=utf-8" });
      saveAs(blob, "tfroam.svg");
    },
  },
  mounted() {
    this.renderGraph();
    if (window.ResizeObserver) {
      this.resizeObserver = new ResizeObserver(() => {
        const cy = this.cy();
        if (cy) cy.resize();
      });
      this.resizeObserver.observe(this.$refs.wrap);
    }
  },
  beforeDestroy() {
    if (this.resizeObserver) this.resizeObserver.disconnect();
    if (this.overlayFrame) window.cancelAnimationFrame(this.overlayFrame);
    this.endDrag();
    window.clearTimeout(this.lineTimer);
  },
};
</script>

<style>
.graph {
  position: relative;
  overflow: hidden;
  height: 100%;
  background: var(--c-canvas);
  outline: none;
}

.graph:focus-visible {
  box-shadow: inset 0 0 0 2px var(--c-accent);
}

/* vue-cytoscape creates this element; the id is also what -genImage waits for.
   font-size stays 16px: the graph's sizes are in em of this element. */
#cytoscape-div {
  height: 100% !important;
  min-height: 0 !important;
  font-size: 16px;
  cursor: grab;
}

#cytoscape-div:active,
.graph.is-panning #cytoscape-div {
  cursor: grabbing;
}

.graph > div:first-child {
  height: 100%;
}

.graph-labels,
.graph-marker,
.graph-tip {
  position: absolute;
  top: 0;
  left: 0;
  pointer-events: none;
}

.graph-label {
  position: absolute;
  top: 0;
  left: 0;
  box-sizing: border-box;
  padding: 1px 5px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font: 600 11px/15px var(--font-mono);
  color: var(--c-muted);
  background: var(--c-surface);
  border-radius: 0 0 4px 0;
}

.graph-label-file {
  color: var(--c-ink);
}

.graph-label-module {
  color: var(--c-on-accent);
  background: var(--c-accent);
  border-radius: 3px;
}

.graph-marker {
  display: none;
  box-sizing: border-box;
  border: 2px solid var(--c-accent);
  border-radius: 4px;
  box-shadow: 0 0 0 3px var(--c-accent-soft);
}

.graph-tip {
  z-index: 3;
  display: flex;
  flex-direction: column;
  max-width: 320px;
  padding: 5px 8px;
  color: var(--c-ink);
  background: var(--c-surface);
  border: 1px solid var(--c-line);
  border-radius: 5px;
  box-shadow: var(--c-shadow);
}

.graph-tip-address {
  font: 600 12px/1.35 var(--font-mono);
  overflow-wrap: anywhere;
}

.graph-tip-meta {
  font-size: 11px;
  color: var(--c-muted);
}

.graph-status {
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  margin: 0;
  text-align: center;
  color: var(--c-muted);
}

.graph-controls {
  position: absolute;
  right: 12px;
  bottom: 12px;
  z-index: 2;
  display: flex;
  flex-direction: column;
  background: var(--c-surface);
  border: 1px solid var(--c-line);
  border-radius: 6px;
  box-shadow: var(--c-shadow);
  overflow: hidden;
}

.graph-controls button {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  color: var(--c-ink);
  background: none;
  border: 0;
  cursor: pointer;
}

.graph-controls button + button {
  border-top: 1px solid var(--c-line-soft);
}

.graph-controls button:hover {
  background: var(--c-sunken);
}

.graph-controls svg {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.6;
  stroke-linecap: round;
  stroke-linejoin: round;
}
</style>
