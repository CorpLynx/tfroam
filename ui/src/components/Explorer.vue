<template>
  <nav class="explorer" aria-label="Resources" @keydown="onKey">
    <template v-if="query.trim()">
      <p class="explorer-head" role="status">
        <template v-if="found.total">
          {{ found.total }} {{ found.total === 1 ? "match" : "matches" }}
          <span v-if="found.total > found.results.length">, first {{ found.results.length }} shown</span>
        </template>
        <template v-else>No address contains “{{ query.trim() }}”</template>
      </p>
      <ul class="results" v-if="found.total">
        <li v-for="(item, i) in found.results" :key="item.id">
          <button
            type="button"
            class="result tree-item"
            :class="{ 'is-selected': item.id === selectedId }"
            :data-key="item.key"
            :tabindex="i === 0 ? 0 : -1"
            @click="$emit('select', item.id)"
          >
            <span v-if="item.action && item.kind === 'resource'" class="act" :class="'act-' + item.action">{{
              symbols[item.action]
            }}</span>
            <span v-else class="kind" :class="'kind-' + item.kind"></span>
            <span class="result-text">
              <span class="result-address mono">{{ breakable(item.id) }}</span>
              <span class="result-where">{{ where(item) }}</span>
            </span>
          </button>
        </li>
      </ul>
      <p class="explorer-note" v-else>Search matches any part of an address, such as a type, a name or a module.</p>
    </template>
    <template v-else>
      <p class="explorer-head">
        <span>Files</span>
        <span class="explorer-actions">
          <button type="button" class="link" @click="setAll(true)">Expand all</button>
          <button type="button" class="link" @click="setAll(false)">Collapse</button>
        </span>
      </p>
      <ul class="tree" role="tree" aria-label="Files and resources">
        <tree-node v-for="file in index.roots" :key="file.key" :node="file" :depth="0" />
      </ul>
    </template>
  </nav>
</template>

<script>
import TreeNode from "@/components/TreeNode.vue";
import { ACTION_SYMBOL, KIND_LABEL } from "@/model";

export default {
  name: "Explorer",
  components: { TreeNode },
  props: {
    index: { type: Object, required: true },
    found: { type: Object, required: true },
    query: { type: String, default: "" },
    selectedId: { type: String, default: "" },
  },
  provide() {
    return { tree: this.tree };
  },
  data() {
    const open = {};
    this.index.roots.forEach((file) => (open[file.key] = true));
    return {
      symbols: ACTION_SYMBOL,
      // Shared with every TreeNode (provide/inject), reactive because it lives in data.
      tree: {
        open,
        selectedId: this.selectedId,
        // One Tab stop for the whole tree (arrow keys move inside it): the row focused last.
        focusKey: this.index.roots.length ? this.index.roots[0].key : "",
        toggle: (key) => {
          this.$set(this.tree.open, key, !this.tree.open[key]);
          if (!this.tree.open[key]) this.tree.focusKey = key; // a row hidden by closing cannot hold the Tab stop
        },
        select: (id) => this.$emit("select", id),
      },
    };
  },
  watch: {
    selectedId(id) {
      this.tree.selectedId = id;
      this.showSelected();
    },
    query() {
      this.showSelected();
    },
  },
  methods: {
    // Let a long address wrap after a dot instead of in the middle of a name.
    breakable(id) {
      return id.replace(/\./g, ".\u200b");
    },
    where(item) {
      const kind = KIND_LABEL[item.kind] || item.kind;
      if (!item.file) return kind;
      return item.line != null ? `${kind} in ${item.file}:${item.line}` : `${kind} in ${item.file}`;
    },
    setAll(open) {
      const next = {};
      const visit = (nodes, depth) =>
        nodes.forEach((n) => {
          // "Collapse" keeps the list of files; "Expand all" opens everything.
          if (n.children.length && (open || depth < 0)) next[n.key] = true;
          visit(n.children, depth + 1);
        });
      visit(this.index.roots, 0);
      this.tree.open = next;
      if (!open && this.index.roots.length) this.tree.focusKey = this.index.roots[0].key;
    },
    // Open the rows above the selected one and bring it into view.
    showSelected() {
      const item = this.index.byId[this.selectedId];
      if (!item) return;
      item.path.forEach((key) => {
        if (!this.tree.open[key]) this.$set(this.tree.open, key, true);
      });
      this.tree.focusKey = item.key;
      this.$nextTick(() => {
        const rows = this.$el.querySelectorAll(".tree-item");
        for (let i = 0; i < rows.length; i++) {
          if (rows[i].getAttribute("data-key") === item.key) {
            if (rows[i].scrollIntoView) rows[i].scrollIntoView({ block: "nearest" });
            break;
          }
        }
      });
    },
    focusFirst() {
      const first = this.$el.querySelector(".tree-item");
      if (first) first.focus();
    },
    onKey(e) {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const rows = Array.prototype.slice.call(this.$el.querySelectorAll(".tree-item"));
      const at = rows.indexOf(document.activeElement);
      if (at === -1) return;
      e.preventDefault();
      const next = rows[at + (e.key === "ArrowDown" ? 1 : -1)];
      if (next) next.focus();
      else if (e.key === "ArrowUp") this.$emit("leave-up");
    },
  },
};
</script>

<style>
.explorer {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--c-surface);
}

.explorer-head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0;
  padding: 8px 12px 6px;
  font-weight: 600;
  color: var(--c-muted);
  border-bottom: 1px solid var(--c-line-soft);
}

.explorer-actions {
  display: flex;
  gap: 10px;
  font-weight: 400;
}

.explorer-note {
  margin: 0;
  padding: 10px 12px;
  color: var(--c-muted);
}

.link {
  padding: 0;
  color: var(--c-accent);
  background: none;
  border: 0;
}

.link:hover {
  text-decoration: underline;
}

.tree,
.tree ul,
.results {
  margin: 0;
  padding: 0;
  list-style: none;
}

.tree,
.results {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 4px 0 12px;
  overscroll-behavior: contain;
}

.tree-row {
  display: flex;
  align-items: center;
  padding-right: 6px;
}

.tree-row:hover,
.result:hover {
  background: var(--c-sunken);
}

.tree-row.is-selected,
.result.is-selected {
  background: var(--c-accent-soft);
  box-shadow: inset 2px 0 0 var(--c-accent);
}

.tree-twist {
  flex: none;
  display: grid;
  place-items: center;
  width: 18px;
  height: 24px;
  padding: 0;
  color: var(--c-faint);
  background: none;
  border: 0;
}

.tree-twist svg {
  width: 12px;
  height: 12px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.6;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: transform 0.12s ease;
}

.tree-twist svg.is-open {
  transform: rotate(90deg);
}

.tree-item {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 4px 0 2px;
  text-align: left;
  background: none;
  border: 0;
  border-radius: 3px;
}

.tree-item:focus-visible {
  outline-offset: -2px;
}

.tree-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 12px;
}

.tree-label.mono {
  display: flex;
}

.tree-label-head {
  flex: 0 1 auto;
  min-width: 2.5em;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--c-muted);
}

.tree-label-tail {
  flex: 0 0 auto;
  max-width: 75%;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tree-label-tail:only-child {
  flex: 0 1 auto;
  max-width: 100%;
}

.is-file > .tree-item > .tree-label {
  font-size: 13px;
  font-weight: 600;
}

.tree-meta {
  flex: none;
  font-size: 11px;
  color: var(--c-faint);
}

.result {
  width: 100%;
  height: auto;
  align-items: flex-start;
  padding: 5px 12px;
  border-radius: 0;
}

.result .act,
.result .kind {
  margin-top: 2px;
}

.result-text {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.result-address {
  font-size: 12px;
  overflow-wrap: anywhere;
}

.result-where {
  font-size: 11px;
  color: var(--c-muted);
}
</style>
