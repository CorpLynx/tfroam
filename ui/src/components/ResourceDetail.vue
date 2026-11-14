<template>
  <section id="resource-details" class="detail" aria-label="Details">
    <header class="detail-head">
      <p class="detail-kind">
        <span v-if="action" class="act" :class="'act-' + action">{{ symbols[action] }}</span>
        <span v-else class="kind" :class="'kind-' + kind"></span>
        <span>{{ kindLabel }}</span>
        <span v-if="action" class="detail-action">{{ actionText }}</span>
        <span v-if="where" class="detail-where mono">{{ where }}</span>
      </p>
      <button type="button" class="btn btn-quiet btn-icon detail-close" title="Close (Esc)" aria-label="Close details" @click="$emit('close')">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8" /></svg>
      </button>
      <h2 class="detail-address mono">{{ id }}</h2>
      <div class="detail-buttons">
        <button type="button" class="btn" @click="copy(id, $event)">Copy address</button>
        <button
          type="button"
          class="btn"
          :disabled="!links.inGraph"
          :title="links.inGraph ? 'Centre the graph on this item' : 'Not drawn in the graph (hidden with -hide, or not part of it)'"
          @click="$emit('zoom', id)"
        >
          Show in graph
        </button>
      </div>
    </header>

    <div class="tabs" role="tablist" aria-label="Details">
      <button
        v-for="t in tabs"
        :key="t.id"
        type="button"
        role="tab"
        :id="'tab-' + t.id"
        :aria-selected="String(tab === t.id)"
        :aria-controls="'panel-' + t.id"
        :tabindex="tab === t.id ? 0 : -1"
        :disabled="t.disabled"
        :class="{ 'is-active': tab === t.id }"
        @click="tab = t.id"
        @keydown.left.prevent="moveTab(-1)"
        @keydown.right.prevent="moveTab(1)"
      >
        {{ t.label }}<span v-if="t.count != null" class="tab-count">{{ t.count }}</span>
      </button>
    </div>

    <div class="detail-body" role="tabpanel" :id="'panel-' + tab" :aria-labelledby="'tab-' + tab" tabindex="0" ref="body">
      <template v-if="tab === 'config'">
        <p v-if="isInstance && configList.length" class="detail-note">
          Written once in
          <button type="button" class="link mono" @click="$emit('select', parentId)">{{ parentId }}</button>
          and shared by every instance.
        </p>
        <p v-if="!configList.length" class="detail-empty">No configuration block was found for this address.</p>
        <dl class="kv">
          <div v-for="row in configList" :key="row.key" class="kv-row">
            <dt class="mono">{{ row.key }}</dt>
            <dd>
              <span v-if="row.refs" class="refs">
                <template v-for="ref in row.refs">
                  <button v-if="ref.target" :key="ref.text" type="button" class="ref mono is-link" :title="'Go to ' + ref.target" @click="$emit('select', ref.target)">
                    {{ ref.text }}
                  </button>
                  <span v-else :key="ref.text" class="ref mono">{{ ref.text }}</span>
                </template>
              </span>
              <pre v-else class="value mono" :class="{ 'is-quiet': row.text === 'null' || row.text === sensitive }">{{ row.text }}</pre>
              <button type="button" class="copy" @click="copy(row.text, $event)">Copy</button>
            </dd>
          </div>
        </dl>
      </template>

      <template v-else-if="tab === 'current' || tab === 'proposed'">
        <template v-if="!stateRows.length">
          <template v-if="instances.length">
            <p class="detail-note">Each instance of this block has its own values:</p>
            <ul class="links">
              <li v-for="other in instances" :key="other">
                <button type="button" class="link mono" @click="$emit('select', other)">{{ other }}</button>
              </li>
            </ul>
          </template>
          <p v-else class="detail-empty">{{ emptyState }}</p>
        </template>
        <dl class="kv">
          <div v-for="row in stateRows" :key="row.key" class="kv-row" :class="{ 'is-changed': row.changed }">
            <dt class="mono">
              {{ row.key }}<span v-if="row.changed" class="changed-mark" title="Differs from the current state">changed</span>
            </dt>
            <dd>
              <pre class="value mono" :class="{ 'is-quiet': row.quiet }">{{ row.text }}</pre>
              <button v-if="!row.unknown" type="button" class="copy" @click="copy(row.text, $event)">Copy</button>
            </dd>
          </div>
        </dl>
      </template>

      <template v-else>
        <p v-if="!links.inGraph" class="detail-empty">
          This item is not drawn in the graph, so its connections are not known here.
        </p>
        <template v-else>
          <p v-if="isInstance && (links.dependsOn.length || links.usedBy.length)" class="detail-note">
            Connections belong to the block, so every instance shares them.
          </p>
          <h3 class="links-head"><span class="line-key line-out"></span>Depends on {{ links.dependsOn.length }}</h3>
          <ul class="links">
            <li v-for="other in links.dependsOn" :key="other">
              <button type="button" class="link mono" @click="$emit('select', other)">{{ other }}</button>
            </li>
          </ul>
          <h3 class="links-head"><span class="line-key line-in"></span>Used by {{ links.usedBy.length }}</h3>
          <ul class="links">
            <li v-for="other in links.usedBy" :key="other">
              <button type="button" class="link mono" @click="$emit('select', other)">{{ other }}</button>
            </li>
          </ul>
        </template>
      </template>
    </div>
  </section>
</template>

<script>
import copyToClipboard from "copy-to-clipboard";
import {
  ACTION_SYMBOL,
  KIND_LABEL,
  SENSITIVE_PLACEHOLDER,
  changeFor,
  configFor,
  configRows,
  formatValue,
  isInstance,
  isUnknown,
  parentAddress,
  resolveReference,
} from "@/model";

export default {
  name: "ResourceDetail",
  props: {
    id: { type: String, required: true },
    rso: { type: Object, required: true },
    index: { type: Object, required: true },
    links: { type: Object, required: true },
  },
  data() {
    return { tab: "config", symbols: ACTION_SYMBOL, sensitive: SENSITIVE_PLACEHOLDER };
  },
  computed: {
    item() {
      return this.index.byId[this.id] || null;
    },
    kind() {
      if (this.item) return this.item.kind;
      // Not in the file map (for example a plan without configuration): go by the address.
      const tail = this.id.replace(/^(module\.[^.]+\.)+/, "");
      if (tail.indexOf("data.") === 0) return "data";
      if (tail.indexOf("output.") === 0) return "output";
      if (tail.indexOf("var.") === 0) return "variable";
      if (tail.indexOf("local.") === 0) return "locals";
      return this.id.indexOf("module.") === 0 && !tail.includes(".") ? "module" : "resource";
    },
    kindLabel() {
      return KIND_LABEL[this.kind] || this.kind;
    },
    where() {
      if (!this.item || !this.item.file) return "";
      return this.item.line != null ? `${this.item.file}:${this.item.line}` : this.item.file;
    },
    isInstance() {
      return isInstance(this.id);
    },
    parentId() {
      return parentAddress(this.id);
    },
    change() {
      return changeFor(this.rso, this.id);
    },
    // A block with instances has no action of its own: the list already worked out what its instances share.
    action() {
      if (this.change.action) return this.change.action;
      return this.item && this.item.kind === "resource" ? this.item.action : "";
    },
    actionText() {
      return { "no-op": "no change", mixed: "mixed actions" }[this.action] || this.action;
    },
    instances() {
      return this.item && this.item.kind !== "module" ? this.item.children.map((c) => c.id) : [];
    },
    emptyState() {
      if (this.tab === "current") {
        return this.action === "create" ? "Nothing exists yet: this plan creates it." : "The plan records no current state for this address.";
      }
      return this.action === "delete" ? "Nothing remains: this plan destroys it." : "The plan records no proposed values for this address.";
    },
    hasState() {
      return !this.id.includes("var.") && this.kind !== "locals";
    },
    configList() {
      return configRows(configFor(this.rso, this.id)).map((row) => {
        if (!row.refs) return row;
        const refs = row.refs.map((text) => ({ text, target: resolveReference(this.index, text, this.id) }));
        return Object.assign({}, row, { refs: refs.map((r) => (r.target === this.id ? { text: r.text, target: "" } : r)) });
      });
    },
    stateRows() {
      const before = this.change.before || null;
      const values = this.tab === "current" ? before : this.change.after;
      if (!values || typeof values !== "object") return [];
      return Object.keys(values).map((key) => {
        const value = values[key];
        const unknown = isUnknown(value);
        const text = unknown ? "known after apply" : formatValue(value);
        return {
          key,
          text,
          unknown,
          quiet: unknown || value === null || value === undefined || text === SENSITIVE_PLACEHOLDER,
          changed:
            this.tab === "proposed" && !!before && JSON.stringify(before[key]) !== JSON.stringify(value),
        };
      });
    },
    tabs() {
      const total = this.links.dependsOn.length + this.links.usedBy.length;
      return [
        { id: "config", label: "Config" },
        { id: "current", label: "Current", disabled: !this.hasState },
        { id: "proposed", label: "Proposed", disabled: !this.hasState },
        { id: "links", label: "Connections", count: this.links.inGraph ? total : null },
      ];
    },
  },
  watch: {
    id() {
      if (!this.hasState && (this.tab === "current" || this.tab === "proposed")) this.tab = "config";
      if (this.$refs.body) this.$refs.body.scrollTop = 0;
    },
  },
  methods: {
    moveTab(step) {
      const usable = this.tabs.filter((t) => !t.disabled);
      const at = usable.findIndex((t) => t.id === this.tab);
      const next = usable[(at + step + usable.length) % usable.length];
      this.tab = next.id;
      this.$nextTick(() => {
        const el = this.$el.querySelector(`#tab-${next.id}`);
        if (el) el.focus();
      });
    },
    copy(text, event) {
      const button = event.currentTarget;
      const label = button.getAttribute("data-label") || button.innerText;
      button.setAttribute("data-label", label);
      copyToClipboard(String(text));
      button.innerText = "Copied";
      window.setTimeout(() => {
        button.innerText = label;
      }, 1000);
    },
  },
};
</script>

<style>
.detail {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--c-surface);
  border: 1px solid var(--c-line);
  border-radius: 8px;
  box-shadow: var(--c-shadow);
  overflow: hidden;
}

.detail-head {
  flex: none;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 8px;
  padding: 10px 8px 10px 14px;
}

.detail-kind {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0;
  min-height: 28px;
  color: var(--c-muted);
}

.detail-action {
  font-weight: 600;
  color: var(--c-ink);
}

.detail-where {
  font-size: 12px;
}

.detail-where::before {
  content: "in ";
  font-family: var(--font-ui);
}

.detail-address {
  grid-column: 1 / -1;
  margin: 0;
  padding-right: 6px;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
  overflow-wrap: anywhere;
  user-select: all;
}

.detail-buttons {
  grid-column: 1 / -1;
  display: flex;
  gap: 6px;
  margin-top: 4px;
}

.tabs {
  flex: none;
  display: flex;
  gap: 2px;
  padding: 0 10px;
  border-bottom: 1px solid var(--c-line);
}

.tabs button {
  padding: 6px 8px 7px;
  color: var(--c-muted);
  background: none;
  border: 0;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}

.tabs button:hover:not([disabled]) {
  color: var(--c-ink);
}

.tabs button.is-active {
  font-weight: 600;
  color: var(--c-ink);
  border-bottom-color: var(--c-accent);
}

.tabs button[disabled] {
  color: var(--c-faint);
  cursor: default;
}

.tab-count {
  margin-left: 5px;
  font-size: 11px;
  color: var(--c-muted);
}

.detail-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
  padding: 4px 0 10px;
}

.detail-body:focus-visible {
  outline-offset: -2px;
}

.detail-note,
.detail-empty {
  margin: 0;
  padding: 8px 14px;
  color: var(--c-muted);
}

.detail-note .link {
  font-size: 12px;
  text-align: left;
  overflow-wrap: anywhere;
}

.kv {
  margin: 0;
}

.kv-row {
  padding: 6px 14px 7px;
  border-top: 1px solid var(--c-line-soft);
}

.kv-row:first-child {
  border-top: 0;
}

.kv-row.is-changed {
  box-shadow: inset 2px 0 0 var(--c-update);
}

.kv dt {
  font-size: 11px;
  color: var(--c-muted);
}

.changed-mark {
  margin-left: 8px;
  font-family: var(--font-ui);
  color: var(--c-update);
}

.kv dd {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 1px 0 0;
}

.value {
  flex: 1;
  min-width: 0;
  max-height: 16em;
  margin: 0;
  overflow: auto;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.value.is-quiet {
  font-style: italic;
  color: var(--c-faint);
}

.refs {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.ref {
  max-width: 100%;
  padding: 1px 6px;
  font-size: 12px;
  text-align: left;
  overflow-wrap: anywhere;
  color: var(--c-ink);
  background: var(--c-sunken);
  border: 0;
  border-radius: 3px;
}

.ref.is-link {
  color: var(--c-accent);
  background: var(--c-accent-soft);
}

.ref.is-link:hover {
  text-decoration: underline;
}

.copy {
  flex: none;
  padding: 0 4px;
  font-size: 11px;
  color: var(--c-muted);
  background: none;
  border: 0;
  border-radius: 3px;
  opacity: 0;
}

.kv-row:hover .copy,
.copy:focus-visible {
  opacity: 1;
}

.copy:hover {
  color: var(--c-accent);
}

@media (hover: none) {
  .copy {
    opacity: 1;
  }
}

.links-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  padding: 10px 14px 4px;
  font-size: 13px;
  font-weight: 600;
}

.line-key {
  flex: none;
  width: 22px;
  height: 0;
  border-top: 2px solid var(--c-accent);
}

.line-in {
  border-top: 2px dashed var(--c-edge-in);
}

.links {
  margin: 0;
  padding: 0 14px 4px;
  list-style: none;
}

.links li {
  padding: 2px 0;
}

.links .link {
  font-size: 12px;
  text-align: left;
  overflow-wrap: anywhere;
}

.links:empty::after {
  content: "None";
  color: var(--c-faint);
}
</style>
