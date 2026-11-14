// Read-only helpers over the three documents the backend publishes (rso, map, graph).
// Nothing here changes them; the lookups follow upstream Rover's ResourceDetail.vue.

import axios from "axios";

// Standalone mode (index.html opened from disk) loads rso.js / map.js / graph.js as
// scripts that define these globals; the served page fetches the same JSON instead.
export function loadDocuments() {
  /* eslint-disable no-undef */
  if (typeof rso !== "undefined" && typeof map !== "undefined" && typeof graph !== "undefined") {
    return Promise.resolve({ rso, map, graph });
  }
  /* eslint-enable no-undef */
  return Promise.all([axios.get("/api/rso"), axios.get("/api/map"), axios.get("/api/graph")]).then(
    ([r, m, g]) => ({ rso: r.data, map: m.data, graph: g.data })
  );
}

export const KIND_LABEL = {
  resource: "resource",
  data: "data",
  module: "module",
  output: "output",
  variable: "variable",
  locals: "local",
  file: "file",
};

// Terraform's own plan symbols, so colour is never the only signal.
export const ACTION_SYMBOL = {
  create: "+",
  update: "~",
  delete: "-",
  replace: "±",
  read: "<",
  "no-op": "·",
  mixed: "*",
};

export const ACTION_ORDER = ["create", "update", "replace", "delete", "read", "no-op"];

function byLineThenName(a, b) {
  const la = a.line == null ? Infinity : a.line;
  const lb = b.line == null ? Infinity : b.line;
  return la - lb || a.label.localeCompare(b.label);
}

// map.root -> a tree of plain nodes plus a flat list of everything that has an address.
export function buildIndex(mapDoc) {
  const items = [];
  const byId = Object.create(null);

  function walk(children, modulePrefix, file, parent) {
    return Object.keys(children || {}).map((id) => {
      const content = children[id];
      const isFile = content.type === "file";
      const node = {
        key: isFile ? `${modulePrefix}#${id}` : id,
        id: isFile ? "" : id,
        kind: content.type,
        label: isFile ? content.name || id : id.indexOf(modulePrefix) === 0 ? id.slice(modulePrefix.length) : id,
        name: content.name || id,
        action: content.change_action || "",
        line: content.line != null ? content.line : parent && !isFile ? parent.line : null,
        file: isFile ? content.name || id : file,
        parentId: parent && parent.id ? parent.id : "",
        path: parent ? parent.path.concat([parent.key]) : [], // keys of the rows to open to show this one
        children: [],
      };
      if (parent && parent.id && node.kind !== "module" && !isFile && parent.kind !== "module") {
        node.label = node.name; // an instance of a counted / for_each resource: `this[0]`
      }
      const childPrefix = node.kind === "module" ? `${id}.` : modulePrefix;
      node.children = walk(content.children, childPrefix, node.file, node);
      if (node.kind !== "module") node.children.sort(byLineThenName); // a module's children are its files
      if (!node.action && node.children.length && node.kind !== "module") {
        const actions = Array.from(new Set(node.children.map((c) => c.action).filter(Boolean)));
        node.action = actions.length === 1 ? actions[0] : actions.length ? "mixed" : "";
      }
      if (!isFile) {
        node.search = id.toLowerCase();
        items.push(node);
        byId[id] = node;
      }
      return node;
    });
  }

  const roots = walk(mapDoc && mapDoc.root, "", "", null);
  return { roots, items, byId };
}

// Every whitespace-separated term must appear in the address.
export function searchIndex(index, query, limit = 300) {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return { results: [], total: 0 };
  const hits = index.items.filter((item) => terms.every((t) => item.search.indexOf(t) !== -1));
  // Shorter addresses first: `aws_vpc.this` before its fifty dependents.
  hits.sort((a, b) => a.id.length - b.id.length || a.id.localeCompare(b.id));
  return { results: hits.slice(0, limit), total: hits.length };
}

const INDEX_SUFFIX = /\[[^[\]]*\]$/;

export function isInstance(id) {
  return INDEX_SUFFIX.test(id);
}

export function parentAddress(id) {
  return id.replace(INDEX_SUFFIX, "");
}

// The configuration block for an address (an instance shares its parent's block).
export function configFor(rsoDoc, id) {
  const states = (rsoDoc && rsoDoc.states) || {};
  const configs = (rsoDoc && rsoDoc.configs) || {};
  const configId = states[id] && states[id].config_id ? states[id].config_id : id.replace(/\[[^[\]]*\]/g, "");
  const entry = configs[configId];
  if (!entry) return {};
  return entry.variable_config || entry.output_config || entry.module_config || entry.resource_config || {};
}

// Planned change for an address: { action, before, after } with unknown values marked.
export function changeFor(rsoDoc, id) {
  const states = (rsoDoc && rsoDoc.states) || {};
  const rc = {};
  if (id.includes("var.")) return rc;

  if (id.includes("output.")) {
    // Root outputs are stored under their bare name.
    let state = states[id];
    if (!state && id.indexOf("output.") === 0) {
      const bare = states[id.slice("output.".length)];
      if (bare && bare.type === "output") state = bare;
    }
    if (!state || !state.change) return rc;
    const c = state.change;
    if (c.actions) rc.action = c.actions.length > 1 ? "replace" : c.actions[0];
    rc.before = c.before === undefined || c.before === null ? null : { value: c.before };
    rc.after = { value: c.after === undefined ? null : c.after };
    if (c.after_unknown === true) rc.after.value = { unknown: true };
    return rc;
  }

  const state = states[id];
  if (state && state.change) {
    const c = state.change;
    if (c.actions) rc.action = c.actions.length > 1 ? "replace" : c.actions[0];
    rc.before = c.before ? c.before : null;
    rc.after = Object.assign({}, c.after ? c.after : {});
    if (c.after_unknown && typeof c.after_unknown === "object") {
      Object.keys(c.after_unknown).forEach((k) => {
        // `true` means the whole attribute is unknown; an object marks unknown parts inside a known value.
        if (c.after_unknown[k] === true || rc.after[k] === undefined) rc.after[k] = { unknown: true };
      });
    }
  }
  return rc;
}

export function isUnknown(value) {
  return !!value && typeof value === "object" && value.unknown === true && Object.keys(value).length === 1;
}

// Text for a value cell. The backend has already replaced sensitive values with a placeholder.
export function formatValue(value) {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

export const SENSITIVE_PLACEHOLDER = "Sensitive Value";

const HIDDEN_CONFIG_KEYS = ["address", "mode", "name", "type", "schema_version"];

// Rows for the Config tab: block metadata first, then one row per argument.
export function configRows(config) {
  const rows = [];
  const push = (key, value) => {
    if (value && typeof value === "object" && Array.isArray(value.references)) {
      rows.push({ key, refs: value.references, text: value.references.join(", ") });
    } else if (key === "depends_on" && Array.isArray(value) && value.every((v) => typeof v === "string")) {
      rows.push({ key, refs: value, text: value.join(", ") });
    } else if (value && typeof value === "object" && "constant_value" in value) {
      rows.push({ key, text: formatValue(value.constant_value) });
    } else {
      rows.push({ key, text: formatValue(value) });
    }
  };
  Object.keys(config || {}).forEach((key) => {
    if (HIDDEN_CONFIG_KEYS.includes(key)) return;
    if (key === "for_each_expression") push("for_each", config[key]);
    else if (key === "count_expression") push("count", config[key]);
    else if (key === "provider_config_key") push("provider", config[key]);
    else if (key !== "expressions") push(key, config[key]);
  });
  const expressions = (config && config.expressions) || {};
  Object.keys(expressions).forEach((key) => push(key, expressions[key]));
  return rows;
}

// A reference such as `aws_vpc.this.id` -> the address it belongs to, if the plan has one.
export function resolveReference(index, ref, fromId) {
  const prefixes = [];
  const parts = fromId.split(".");
  for (let i = 0; i + 1 < parts.length && parts[i] === "module"; i += 2) {
    prefixes.unshift(parts.slice(0, i + 2).join(".") + ".");
  }
  prefixes.push("");
  const segments = ref.split(".");
  for (let n = segments.length; n >= 2; n--) {
    const candidate = segments.slice(0, n).join(".");
    for (let p = 0; p < prefixes.length; p++) {
      if (index.byId[prefixes[p] + candidate]) return prefixes[p] + candidate;
    }
  }
  return "";
}
