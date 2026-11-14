// Colour tokens for the page and the graph canvas. One table per theme, so the
// CSS custom properties (chrome) and the cytoscape stylesheet (canvas) cannot drift apart.

export const PALETTES = {
  light: {
    canvas: "#f4f5f7",
    surface: "#ffffff",
    sunken: "#eceef1",
    line: "#d5d9df",
    lineSoft: "#e6e9ed",
    ink: "#1a1d21",
    muted: "#5b6470",
    faint: "#8a929c",
    accent: "#6d3fc0", // Terraform purple, darkened for text contrast
    accentSoft: "#f0eafb",
    onAccent: "#ffffff",
    create: "#1e8e4e",
    update: "#1b6fd1",
    delete: "#cf3030",
    replace: "#e0a526",
    onReplace: "#2b1d00",
    none: "#7b8490",
    data: "#c43d73",
    dataSoft: "#fbe9f0",
    output: "#c98a00",
    outputSoft: "#fdf4d8",
    variable: "#1b6fd1",
    variableSoft: "#e4f0fd",
    local: "#1a1d21",
    onLocal: "#ffffff",
    boxLine: "#aab1ba",
    edge: "#aab1ba",
    edgeIn: "#0b8a8a",
    match: "#f2a900",
    shadow: "0 6px 24px rgba(20, 24, 31, 0.14), 0 1px 3px rgba(20, 24, 31, 0.1)",
  },
  dark: {
    canvas: "#101317",
    surface: "#171b21",
    sunken: "#1e232a",
    line: "#2e353e",
    lineSoft: "#232931",
    ink: "#e7eaee",
    muted: "#9aa4b0",
    faint: "#6f7985",
    accent: "#b392f0",
    accentSoft: "#261f38",
    onAccent: "#14101f",
    create: "#2ea663",
    update: "#3d8be6",
    delete: "#e05252",
    replace: "#d9a02b",
    onReplace: "#201500",
    none: "#5d6672",
    data: "#e06a99",
    dataSoft: "#331c27",
    output: "#d9a02b",
    outputSoft: "#33290f",
    variable: "#3d8be6",
    variableSoft: "#14263d",
    local: "#e7eaee",
    onLocal: "#101317",
    boxLine: "#56606c",
    edge: "#4a535e",
    edgeIn: "#4fd1c5",
    match: "#ffc233",
    shadow: "0 8px 28px rgba(0, 0, 0, 0.5), 0 1px 3px rgba(0, 0, 0, 0.4)",
  },
};

const STORAGE_KEY = "tfroam-theme";
const MODES = ["auto", "light", "dark"];

function systemTheme() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

// localStorage can be unavailable (file:// in some browsers, private windows).
export function savedMode() {
  try {
    const mode = window.localStorage.getItem(STORAGE_KEY);
    return MODES.includes(mode) ? mode : "auto";
  } catch (e) {
    return "auto";
  }
}

export function saveMode(mode) {
  try {
    window.localStorage.setItem(STORAGE_KEY, mode);
  } catch (e) {
    // the choice then lasts for this page view only
  }
}

export function nextMode(mode) {
  return MODES[(MODES.indexOf(mode) + 1) % MODES.length];
}

export function resolveTheme(mode) {
  return mode === "auto" ? systemTheme() : mode;
}

// Writes the palette to :root as --c-<token> and returns it.
export function applyTheme(theme) {
  const palette = PALETTES[theme];
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  Object.keys(palette).forEach((name) => {
    root.style.setProperty(`--c-${name.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase())}`, palette[name]);
  });
  return palette;
}

export function onSystemThemeChange(callback) {
  if (!window.matchMedia) return;
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  if (query.addEventListener) query.addEventListener("change", callback);
  else if (query.addListener) query.addListener(callback);
}

export function prefersReducedMotion() {
  return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}
