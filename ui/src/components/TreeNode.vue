<template>
  <li role="none">
    <div
      class="tree-row"
      :class="{ 'is-selected': node.id && node.id === tree.selectedId, 'is-file': isFile }"
      :style="{ paddingLeft: depth * 14 + 4 + 'px' }"
    >
      <button
        v-if="node.children.length"
        type="button"
        class="tree-twist"
        tabindex="-1"
        :aria-label="(open ? 'Collapse ' : 'Expand ') + node.label"
        @click="tree.toggle(node.key)"
      >
        <svg viewBox="0 0 12 12" :class="{ 'is-open': open }" aria-hidden="true"><path d="M4.5 2.5 8 6l-3.5 3.5" /></svg>
      </button>
      <span v-else class="tree-twist"></span>
      <button
        type="button"
        class="tree-item"
        role="treeitem"
        :aria-expanded="node.children.length ? String(open) : null"
        :aria-selected="node.id ? String(node.id === tree.selectedId) : null"
        :data-key="node.key"
        :tabindex="node.key === tree.focusKey ? 0 : -1"
        :title="node.id || node.label"
        @focus="tree.focusKey = node.key"
        @click="activate"
        @keydown.right.prevent="node.children.length && !open && tree.toggle(node.key)"
        @keydown.left.prevent="open && tree.toggle(node.key)"
      >
        <span v-if="isFile" class="tree-label">{{ node.label }}</span>
        <template v-else>
          <span v-if="hasAction" class="act" :class="'act-' + node.action">{{ symbol }}</span>
          <span v-else class="kind" :class="'kind-' + node.kind"></span>
          <span class="tree-label mono"
            ><span class="tree-label-head" v-if="labelHead">{{ labelHead }}</span
            ><span class="tree-label-tail">{{ labelTail }}</span></span
          >
        </template>
        <span class="tree-meta">{{ meta }}</span>
      </button>
    </div>
    <ul v-if="open && node.children.length" role="group">
      <tree-node v-for="child in node.children" :key="child.key" :node="child" :depth="depth + 1" />
    </ul>
  </li>
</template>

<script>
import { ACTION_SYMBOL, KIND_LABEL } from "@/model";

export default {
  name: "TreeNode",
  inject: ["tree"],
  props: {
    node: { type: Object, required: true },
    depth: { type: Number, default: 0 },
  },
  computed: {
    isFile() {
      return this.node.kind === "file";
    },
    open() {
      return !!this.tree.open[this.node.key];
    },
    // `aws_ec2_transit_gateway_route.` may be cut short; the name after it never is.
    splitAt() {
      const isBlock = (this.node.kind === "resource" || this.node.kind === "data") && !/\[[^\]]*\]$/.test(this.node.label);
      return isBlock ? this.node.label.lastIndexOf(".") + 1 : 0;
    },
    labelHead() {
      return this.node.label.slice(0, this.splitAt);
    },
    labelTail() {
      return this.node.label.slice(this.splitAt);
    },
    hasAction() {
      return !!this.node.action && this.node.kind === "resource";
    },
    symbol() {
      return ACTION_SYMBOL[this.node.action] || "";
    },
    meta() {
      const n = this.node.children.length;
      if (this.isFile) return String(n);
      if (this.node.kind === "module") return "module";
      if (this.node.kind === "resource") return n ? `×${n}` : "";
      return KIND_LABEL[this.node.kind] || "";
    },
  },
  methods: {
    activate() {
      if (this.isFile) this.tree.toggle(this.node.key);
      else this.tree.select(this.node.id);
    },
  },
};
</script>
