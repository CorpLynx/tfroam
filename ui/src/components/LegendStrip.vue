<template>
  <div class="legend" :class="{ 'is-closed': !open }">
    <button type="button" class="legend-toggle" :aria-expanded="String(open)" aria-controls="legend-items" @click="open = !open">
      Key
    </button>
    <ul id="legend-items" v-show="open" aria-label="What the colours mean">
      <li v-for="entry in entries" :key="entry.id" :title="entry.title">
        <span v-if="entry.symbol" class="act" :class="'act-' + entry.id">{{ entry.symbol }}</span>
        <span v-else class="kind" :class="'kind-' + entry.id"></span>
        {{ entry.label }}
        <span class="legend-count">{{ entry.count }}</span>
      </li>
      <template v-if="hasSelection">
        <li class="legend-line"><span class="line-key line-out"></span>depends on</li>
        <li class="legend-line"><span class="line-key line-in"></span>used by</li>
      </template>
    </ul>
  </div>
</template>

<script>
import { ACTION_ORDER, ACTION_SYMBOL } from "@/model";

const KINDS = [
  ["data", "data source"],
  ["output", "output"],
  ["variable", "variable"],
  ["locals", "local"],
  ["module", "module"],
];

export default {
  name: "LegendStrip",
  props: {
    graphData: { type: Object, default: null },
    hasSelection: { type: Boolean, default: false },
  },
  data() {
    return { open: true };
  },
  computed: {
    // Only what this plan contains, with how many: the key doubles as the plan summary.
    entries() {
      const actions = {};
      const kinds = {};
      ((this.graphData && this.graphData.nodes) || []).forEach((n) => {
        const type = n.data.type;
        const classes = ` ${n.classes} `;
        if (type === "resource" && classes.includes(" resource-name ")) {
          const action = n.data.change || "unchanged";
          actions[action] = (actions[action] || 0) + 1;
        } else if (type === "data" && classes.includes(" data-name ")) {
          kinds.data = (kinds.data || 0) + 1;
        } else if (["output", "variable", "locals", "module"].includes(type)) {
          kinds[type] = (kinds[type] || 0) + 1;
        }
      });
      const out = [];
      ACTION_ORDER.concat(["unchanged"]).forEach((id) => {
        if (!actions[id]) return;
        out.push({
          id,
          symbol: ACTION_SYMBOL[id] || "·",
          label: id === "no-op" ? "no change" : id === "unchanged" ? "not in plan" : id,
          count: actions[id],
          title: `${actions[id]} resource instances: ${id}`,
        });
      });
      KINDS.forEach(([id, label]) => {
        if (kinds[id]) out.push({ id, label, count: kinds[id], title: `${kinds[id]} ${label}s` });
      });
      return out;
    },
  },
};
</script>

<style>
.legend {
  position: absolute;
  left: 12px;
  bottom: 12px;
  z-index: 2;
  display: flex;
  align-items: center;
  max-width: calc(100% - 76px);
  background: var(--c-surface);
  border: 1px solid var(--c-line);
  border-radius: 6px;
  box-shadow: var(--c-shadow);
}

.legend-toggle {
  align-self: stretch;
  padding: 0 9px;
  font-weight: 600;
  color: var(--c-muted);
  background: none;
  border: 0;
  border-right: 1px solid var(--c-line-soft);
  border-radius: 5px 0 0 5px;
}

.legend.is-closed .legend-toggle {
  height: 28px;
  border-right: 0;
  border-radius: 5px;
}

.legend-toggle:hover {
  color: var(--c-ink);
  background: var(--c-sunken);
}

.legend ul {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 14px;
  margin: 0;
  padding: 5px 10px;
  list-style: none;
}

.legend li {
  display: flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
  font-size: 12px;
}

.legend-count {
  color: var(--c-muted);
  font-variant-numeric: tabular-nums;
}

.legend-line {
  color: var(--c-muted);
}
</style>
