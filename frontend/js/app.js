// NEXUS BI dashboard: boot, navigation, global filters, theme and view rendering.

import * as api from "./api.js";
import * as state from "./state.js";
import * as ribbon from "./ribbon.js";
import { dayLabel, category as catName } from "./format.js";
import { errorBox, h } from "./components.js";
import views from "./views/index.js";

const $ = (id) => document.getElementById(id);
const viewEl = $("view");
let renderToken = 0;
let appliedFilters = null;

// ---------------------------------------------------------------- Navigation

const NAV_ICONS = {
  ask: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20.5l1.4-5.1A8 8 0 1 1 21 12z"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.3 2.4c-.5.2-.8.6-.8 1.1v.5M12 16.5v.3"/>',
  overview: '<path d="M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z"/>',
  sales: '<path d="M4 20V11M10 20V5M16 20v-8M22 20H2"/>',
  customers: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5a6.5 6.5 0 0 1 3.5 5.5"/>',
  products: '<path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z"/><path d="m3 7.5 9 4.5 9-4.5M12 12v9"/>',
  geography: '<path d="M12 21s7-6.2 7-11.5a7 7 0 1 0-14 0C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
  segments: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  churn: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 11h6"/>',
  forecast: '<path d="M3 17l5-5 4 3 5-7"/><path d="M17 8h4v4" /><path d="M3 21h18" stroke-dasharray="2 3"/>',
  anomalies: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  quality: '<path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
};

function buildNav() {
  const nav = $("nav");
  const groups = [...new Set(views.map((v) => v.group))];
  for (const group of groups) {
    const box = h("div", { class: "nav-group" }, h("div", { class: "nav-group-title" }, group));
    for (const v of views.filter((x) => x.group === group)) {
      box.append(h("a", { href: `#/${v.id}`, "data-view": v.id,
        html: `<svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${NAV_ICONS[v.id]}</svg><span>${v.title}</span>` }));
    }
    nav.append(box);
  }
}

// Default landing view is the Overview; "Ask your data" sits first in the navigation.
const currentView = () => views.find((v) => `#/${v.id}` === location.hash) || views.find((v) => v.id === "overview");

// ---------------------------------------------------------------- Filters

function buildFilters(meta) {
  const period = $("f-period");
  for (const p of state.PRESETS) period.append(h("option", { value: p.id }, p.label));
  for (const s of meta.states) $("f-state").append(h("option", { value: s.state_code }, `${s.state_name} (${s.state_code})`));
  for (const c of meta.categories) $("f-category").append(h("option", { value: c.category_key }, catName(c.category)));

  period.addEventListener("change", () => { if (period.value !== "custom") state.set({ preset: period.value }); });
  $("f-state").addEventListener("change", (e) => state.set({ state: e.target.value }));
  $("f-category").addEventListener("change", (e) => state.set({ category: e.target.value }));
  $("f-reset").addEventListener("click", () => state.reset());
}

function syncFilterControls() {
  const f = state.get();
  $("f-period").value = f.preset;
  $("f-period").querySelector('option[value="custom"]').hidden = f.preset !== "custom";
  $("f-state").value = f.state;
  $("f-category").value = f.category;
}

// ---------------------------------------------------------------- Theme

function initTheme() {
  const btn = $("theme-toggle");
  const apply = (dark) => {
    if (dark) document.documentElement.dataset.theme = "dark"; else delete document.documentElement.dataset.theme;
    btn.setAttribute("aria-pressed", String(dark));
    btn.setAttribute("aria-label", dark ? "Light mode" : "Dark mode");
    try { localStorage.setItem("nexus-theme", dark ? "dark" : "light"); } catch (e) { /* storage unavailable */ }
  };
  btn.setAttribute("aria-pressed", String(document.documentElement.dataset.theme === "dark"));
  btn.addEventListener("click", () => {
    apply(document.documentElement.dataset.theme !== "dark");
    renderRibbon();
    render({ keepScroll: true });
  });
}

// ---------------------------------------------------------------- Ribbon

async function loadRibbon() {
  const f = state.get();
  const rows = await api.get("/sales/monthly", { state: f.state || null, category: f.category || null });
  ribbon.setData(rows);
}

function renderRibbon() {
  if (appliedFilters) ribbon.render(appliedFilters, state.getMeta());
}

// ---------------------------------------------------------------- Rendering

async function render({ keepScroll = false } = {}) {
  const token = ++renderToken;
  const view = currentView();
  document.querySelectorAll("#nav a").forEach((a) => a.toggleAttribute("aria-current", a.dataset.view === view.id));
  document.querySelectorAll("#nav a[aria-current]").forEach((a) => a.setAttribute("aria-current", "page"));
  $("view-title").textContent = view.title;
  $("view-subtitle").textContent = view.subtitle;
  document.title = `${view.title} | NEXUS BI`;
  // Show only the global filters this view honours (default: all of them).
  const applies = view.filters || ["period", "state", "category"];
  $("f-period").closest(".field").hidden = !applies.includes("period");
  $("f-state").closest(".field").hidden = !applies.includes("state");
  $("f-category").closest(".field").hidden = !applies.includes("category");
  $("f-reset").hidden = applies.length === 0;
  $("ribbon").hidden = !applies.includes("period");

  viewEl.classList.add("is-loading");  // keep the previous render visible while loading (no layout jump)
  try {
    const filters = await api.get("/kpis", state.params()).then((r) => r.filters);  // validated filters + comparison period
    await loadRibbon();
    if (token !== renderToken) return;
    appliedFilters = filters;
    renderRibbon();
    const fragment = h("div", { style: "display:contents" });
    await view.render(fragment, { filters, params: state.params(), meta: state.getMeta(), state: state.get() });
    if (token !== renderToken) return;
    viewEl.replaceChildren(fragment);
    if (!keepScroll) window.scrollTo({ top: 0 });
  } catch (error) {
    if (token !== renderToken) return;
    console.error(error);
    viewEl.replaceChildren(errorBox(error));
  } finally {
    if (token === renderToken) viewEl.classList.remove("is-loading");
  }
}

// ---------------------------------------------------------------- Boot

async function boot() {
  buildNav();
  initTheme();
  $("menu-toggle").addEventListener("click", () => {
    const open = $("sidebar").classList.toggle("open");
    $("menu-toggle").setAttribute("aria-expanded", String(open));
  });
  $("nav").addEventListener("click", () => { $("sidebar").classList.remove("open"); $("menu-toggle").setAttribute("aria-expanded", "false"); });

  let meta;
  try {
    meta = await api.get("/meta");
  } catch (error) {
    viewEl.replaceChildren(errorBox(error));
    return;
  }
  state.init(meta);
  buildFilters(meta);
  syncFilterControls();
  const p = meta.reporting_period;
  $("data-window").textContent = `Reliable data ${dayLabel(p.data_start)} to ${dayLabel(p.data_end)}`;

  state.subscribe(() => { syncFilterControls(); render({ keepScroll: true }); });
  window.addEventListener("hashchange", () => render());
  // Views can ask to be redrawn after a local control changes (e.g. a ranking toggle).
  document.addEventListener("nexus:rerender", () => render({ keepScroll: true }));
  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderRibbon, 150); });
  await render();
}

boot();
