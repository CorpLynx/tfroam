<template>
  <header class="bar">
    <button
      type="button"
      class="btn btn-quiet btn-icon"
      :aria-pressed="String(listOpen)"
      :title="listOpen ? 'Hide the resource list' : 'Show the resource list'"
      aria-label="Resource list"
      @click="$emit('toggle-list')"
    >
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 3.5h11v9h-11zM6.5 3.5v9" /></svg>
    </button>
    <h1 class="bar-title" :title="path">
      <span class="bar-brand">tfroam</span>
      <span class="bar-dir mono" v-if="dirName">{{ dirName }}</span>
    </h1>

    <div class="search" role="search">
      <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.2" /><path d="m10.2 10.2 3.3 3.3" /></svg>
      <input
        ref="input"
        type="search"
        class="mono"
        placeholder="Find an address"
        aria-label="Find a resource by address"
        autocomplete="off"
        spellcheck="false"
        :value="query"
        @input="$emit('update:query', $event.target.value)"
        @keydown.enter.prevent="$emit('enter')"
        @keydown.down.prevent="$emit('down')"
        @keydown.esc.prevent.stop="onEsc"
      />
      <kbd v-if="!query" aria-hidden="true">/</kbd>
    </div>

    <div class="bar-end">
      <button type="button" class="btn btn-quiet" :title="'Colours: ' + mode + '. Click to change.'" @click="$emit('cycle-theme')">
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="5.5" />
          <path d="M8 2.5v11a5.500 5.500 0 0 0 0-11z" fill="currentColor" />
        </svg>
        <span class="bar-mode">{{ modeLabel }}</span>
      </button>
      <!-- The id is what `tfroam -genImage` clicks. -->
      <button id="saveGraph" type="button" class="btn" title="Download the whole graph as tfroam.svg" @click="$emit('save')">
        Save SVG
      </button>
    </div>
  </header>
</template>

<script>
export default {
  name: "TopBar",
  props: {
    dirName: { type: String, default: "" },
    path: { type: String, default: "" },
    query: { type: String, default: "" },
    mode: { type: String, default: "auto" },
    listOpen: { type: Boolean, default: true },
  },
  computed: {
    modeLabel() {
      return { auto: "Auto", light: "Light", dark: "Dark" }[this.mode];
    },
  },
  methods: {
    focusSearch() {
      this.$refs.input.focus();
      this.$refs.input.select();
    },
    onEsc() {
      if (this.query) this.$emit("update:query", "");
      else this.$refs.input.blur();
    },
  },
};
</script>

<style>
.bar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  height: var(--bar-h);
  padding: 0 10px 0 8px;
  background: var(--c-surface);
  border-bottom: 1px solid var(--c-line);
}

.bar-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
  margin: 0 6px 0 0;
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
}

.bar-brand {
  letter-spacing: 0.01em;
}

.bar-dir {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  font-weight: 400;
  color: var(--c-muted);
}

.search {
  position: relative;
  flex: 1 1 320px;
  max-width: 460px;
  min-width: 140px;
}

.search svg {
  position: absolute;
  top: 7px;
  left: 8px;
  width: 14px;
  height: 14px;
  fill: none;
  stroke: var(--c-muted);
  stroke-width: 1.5;
  stroke-linecap: round;
  pointer-events: none;
}

.search input {
  width: 100%;
  height: 28px;
  padding: 0 28px 0 28px;
  font-size: 12px;
  color: var(--c-ink);
  background: var(--c-sunken);
  border: 1px solid transparent;
  border-radius: 5px;
  outline: none;
}

.search input::placeholder {
  font-family: var(--font-ui);
  font-size: 13px;
  color: var(--c-muted);
}

.search input:focus {
  background: var(--c-surface);
  border-color: var(--c-accent);
  box-shadow: 0 0 0 2px var(--c-accent-soft);
}

.search kbd {
  position: absolute;
  top: 5px;
  right: 6px;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  font: 11px/16px var(--font-mono);
  text-align: center;
  color: var(--c-muted);
  border: 1px solid var(--c-line);
  border-radius: 3px;
  pointer-events: none;
}

.bar-end {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}

@media (max-width: 760px) {
  .bar-dir,
  .bar-mode {
    display: none;
  }
}
</style>
