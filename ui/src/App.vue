<template>
  <div id="app" class="app" :class="{ 'list-open': listOpen }">
    <top-bar
      ref="bar"
      :dir-name="dirName"
      :path="path"
      :query="query"
      :mode="mode"
      :list-open="listOpen"
      @update:query="setQuery"
      @enter="selectFirstResult"
      @down="focusList"
      @toggle-list="listOpen = !listOpen"
      @cycle-theme="cycleTheme"
      @save="saveGraph"
    />
    <div class="work">
      <aside class="side" v-if="index" v-show="listOpen">
        <explorer
          ref="explorer"
          :index="index"
          :found="found"
          :query="query"
          :selected-id="selectedId"
          @select="selectFromList"
          @leave-up="focusSearch"
        />
      </aside>
      <main class="stage" :class="{ 'has-detail': !!(docs && selectedId) }">
        <graph
          ref="graph"
          :graph-data="docs && docs.graph"
          :selected-id="selectedId"
          :matches="matchIds"
          :palette="palette"
          :right-inset="rightInset"
          :bottom-inset="bottomInset"
          @select="selectFromGraph"
          @links="links = $event"
        />
        <p class="stage-message" v-if="message" role="status">{{ message }}</p>
        <legend-strip v-if="docs" :graph-data="docs.graph" :has-selection="links.inGraph" />
        <resource-detail
          v-if="docs && selectedId"
          ref="detail"
          class="stage-detail"
          :id="selectedId"
          :rso="docs.rso"
          :index="index"
          :links="links"
          @select="selectFromList"
          @zoom="zoomTo"
          @close="select('')"
        />
      </main>
    </div>
  </div>
</template>

<script>
import TopBar from "@/components/TopBar.vue";
import Explorer from "@/components/Explorer.vue";
import Graph from "@/components/Graph/Graph.vue";
import LegendStrip from "@/components/LegendStrip.vue";
import ResourceDetail from "@/components/ResourceDetail.vue";
import { buildIndex, loadDocuments, searchIndex } from "@/model";
import { applyTheme, nextMode, onSystemThemeChange, resolveTheme, savedMode, saveMode } from "@/theme";

const NARROW = 900; // below this the resource list opens over the graph instead of beside it
const NO_LINKS = { inGraph: false, dependsOn: [], usedBy: [] };

export default {
  name: "App",
  metaInfo() {
    return { title: this.dirName ? `${this.dirName} | tfroam` : "tfroam | Terraform visualizer" };
  },
  components: { TopBar, Explorer, Graph, LegendStrip, ResourceDetail },
  data() {
    const mode = savedMode();
    return {
      // The documents are large and never change: keep them out of Vue's reactivity.
      docs: null,
      index: null,
      message: "Loading the plan…",
      selectedId: "",
      links: NO_LINKS,
      query: "",
      found: { results: [], total: 0 },
      mode,
      palette: applyTheme(resolveTheme(mode)),
      listOpen: window.innerWidth >= NARROW,
      rightInset: 0,
      bottomInset: 0,
    };
  },
  computed: {
    path() {
      return (this.docs && this.docs.map && this.docs.map.path) || "";
    },
    dirName() {
      return this.path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
    },
    matchIds() {
      return this.found.results.map((item) => item.id);
    },
  },
  methods: {
    setQuery(value) {
      this.query = value;
      window.clearTimeout(this.searchTimer);
      // Typing must stay instant on a big plan: search after a short pause.
      this.searchTimer = window.setTimeout(() => {
        this.found = this.index ? searchIndex(this.index, this.query) : { results: [], total: 0 };
        if (this.query.trim() && !this.listOpen) this.listOpen = true;
      }, 90);
    },
    select(id) {
      this.selectedId = id || "";
      if (!id) this.links = NO_LINKS;
      this.$nextTick(this.measureDetail);
    },
    selectFromGraph(id) {
      this.select(id);
      // Two ticks: the panel has to render and be measured before the graph knows what it covers.
      if (id) this.$nextTick(() => this.$nextTick(() => this.$refs.graph.keepClear(id)));
    },
    // From the list, search or a link in the details: also bring the node into view.
    selectFromList(id) {
      this.select(id);
      if (window.innerWidth < NARROW) this.listOpen = false;
      this.$nextTick(() => this.$nextTick(() => this.$refs.graph.reveal(id)));
    },
    selectFirstResult() {
      window.clearTimeout(this.searchTimer);
      this.found = this.index ? searchIndex(this.index, this.query) : this.found;
      if (this.found.results.length) this.selectFromList(this.found.results[0].id);
    },
    zoomTo(id) {
      this.$refs.graph.zoomTo(id);
    },
    saveGraph() {
      this.$refs.graph.saveGraph();
    },
    focusSearch() {
      this.$refs.bar.focusSearch();
    },
    focusList() {
      if (!this.listOpen) this.listOpen = true;
      this.$nextTick(() => this.$refs.explorer && this.$refs.explorer.focusFirst());
    },
    cycleTheme() {
      this.mode = nextMode(this.mode);
      saveMode(this.mode);
      this.palette = applyTheme(resolveTheme(this.mode));
    },
    // The details panel floats over the right of the graph; "show in graph" centres in what is left.
    measureDetail() {
      const el = this.$refs.detail && this.$refs.detail.$el;
      const beside = window.innerWidth > 700; // must match the media query below
      this.rightInset = el && beside ? el.offsetWidth + 24 : 0;
      this.bottomInset = el && !beside ? el.offsetHeight + 16 : 0;
    },
    onKey(e) {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
      if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        this.focusSearch();
      } else if (e.key === "Escape" && !typing && this.selectedId) {
        this.select("");
      }
    },
  },
  created() {
    loadDocuments()
      .then((docs) => {
        this.index = Object.freeze(buildIndex(docs.map));
        this.docs = Object.freeze(docs);
        this.message = "";
      })
      .catch(() => {
        this.message = "The plan data could not be loaded. Check that rover is still running, then reload.";
      });
  },
  mounted() {
    window.addEventListener("keydown", this.onKey);
    window.addEventListener("resize", this.measureDetail);
    onSystemThemeChange(() => {
      if (this.mode === "auto") this.palette = applyTheme(resolveTheme("auto"));
    });
  },
  beforeDestroy() {
    window.removeEventListener("keydown", this.onKey);
    window.removeEventListener("resize", this.measureDetail);
  },
};
</script>

<style>
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  min-height: 0;
}

.work {
  position: relative;
  flex: 1;
  display: flex;
  min-height: 0;
}

.side {
  flex: none;
  width: var(--side-w);
  min-height: 0;
  border-right: 1px solid var(--c-line);
}

.stage {
  position: relative;
  flex: 1;
  min-width: 0;
  min-height: 0;
}

.stage-message {
  position: absolute;
  top: 45%;
  left: 0;
  right: 0;
  margin: 0;
  padding: 0 24px;
  text-align: center;
  color: var(--c-muted);
  pointer-events: none;
}

.stage-detail {
  position: absolute;
  top: 12px;
  right: 12px;
  bottom: 12px;
  z-index: 4;
  width: min(var(--detail-w), calc(100% - 24px));
}

/* The zoom buttons and the key move out from under the details panel. */
.has-detail .graph-controls {
  right: calc(min(var(--detail-w), 100% - 24px) + 24px);
}

.has-detail .legend {
  max-width: calc(100% - var(--detail-w) - 100px);
}

@media (max-width: 899px) {
  .side {
    position: absolute;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: 6;
    width: min(var(--side-w), 86vw);
    box-shadow: var(--c-shadow);
  }
}

@media (max-width: 700px) {
  .stage-detail {
    top: auto;
    left: 8px;
    right: 8px;
    bottom: 8px;
    width: auto;
    height: 58%;
  }

  .has-detail .graph-controls {
    right: 12px;
    bottom: calc(58% + 20px);
  }

  .has-detail .legend {
    display: none;
  }
}
</style>
