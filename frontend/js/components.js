// Reusable UI pieces: panels with a chart/table toggle, sortable tables, KPI ledger, insights.

import { sparkline } from "./charts.js";
import { escapeHtml, monthLabel, pts, signedPct } from "./format.js";

export const icons = {
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M19 12l-7 7-7-7"/></svg>',
  flat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M5 12h14"/></svg>',
  positive: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg>',
  negative: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7l6 6 4-4 8 8"/><path d="M14 17h7v-7"/></svg>',
  neutral: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5v.5"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l9.5 17h-19z"/><path d="M12 10v4M12 17.5v.5"/></svg>',
  table: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10"/></svg>',
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
};

/** Tiny element factory: h("div", {class: "x"}, "text" | Node | html-string via {html}). */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined) el.append(c.nodeType ? c : document.createTextNode(c));
  return el;
}

/**
 * Panel with a title and, optionally, a chart that can be switched to a table view
 * (every chart has an accessible table twin).
 */
export function panel({ title, sub, chartClass = "chart", table, body, actions = [] }) {
  const root = h("section", { class: "panel" });
  const actionsEl = h("div", { class: "panel-actions" }, ...actions);
  root.append(h("div", { class: "panel-head" },
    h("div", {}, h("h2", { class: "panel-title" }, title), sub ? h("p", { class: "panel-sub" }, sub) : null),
    actionsEl));
  const chart = h("div", { class: chartClass });
  const tableHost = h("div", { hidden: true });
  if (body) root.append(body);
  else root.append(chart, tableHost);

  if (table && !body) {
    const btn = h("button", { class: "btn btn-quiet", type: "button", "aria-pressed": "false", html: `${icons.table}<span>Table</span>` });
    btn.addEventListener("click", () => {
      const showTable = btn.getAttribute("aria-pressed") !== "true";
      btn.setAttribute("aria-pressed", String(showTable));
      btn.innerHTML = showTable ? `${icons.chart}<span>Chart</span>` : `${icons.table}<span>Table</span>`;
      chart.hidden = showTable;
      tableHost.hidden = !showTable;
      if (showTable && !tableHost.childElementCount) tableHost.append(dataTable(table()));
      if (!showTable && window.Plotly) window.Plotly.Plots.resize(chart);
    });
    actionsEl.append(btn);
  }
  return { root, chart, tableHost };
}

/**
 * Sortable table. columns: [{key, label, align: "r", format: (v,row)=>string|Node, sort: false}]
 * Values render as text unless format returns a Node.
 */
export function dataTable({ columns, rows, pageSize = 0, maxHeight = true, caption }) {
  let sortKey = null, dir = -1, page = 0;
  const wrap = h("div", {});
  const render = () => {
    const sorted = sortKey === null ? rows : [...rows].sort((a, b) => {
      const x = a[sortKey], y = b[sortKey];
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x > y ? 1 : -1) * dir;
    });
    const visible = pageSize ? sorted.slice(page * pageSize, (page + 1) * pageSize) : sorted;
    const table = h("table", {}, caption ? h("caption", { class: "visually-hidden" }, caption) : null);
    const headRow = h("tr");
    for (const col of columns) {
      const th = h("th", { class: col.align === "r" ? "r" : null, scope: "col",
        "aria-sort": sortKey === col.key ? (dir > 0 ? "ascending" : "descending") : null });
      if (col.sort === false) th.append(col.label);
      else th.append(h("button", { type: "button", onclick: () => { dir = sortKey === col.key ? -dir : -1; sortKey = col.key; page = 0; render(); } }, col.label));
      headRow.append(th);
    }
    table.append(h("thead", {}, headRow));
    const body = h("tbody");
    for (const row of visible) {
      const tr = h("tr");
      for (const col of columns) {
        const value = col.format ? col.format(row[col.key], row) : row[col.key];
        const td = h("td", { class: col.align === "r" ? "r" : null });
        if (value && value.nodeType) td.append(value);
        else td.textContent = value ?? "–";
        tr.append(td);
      }
      body.append(tr);
    }
    if (!visible.length) body.append(h("tr", {}, h("td", { colspan: columns.length, class: "cell-muted" }, "No rows match the current filters.")));
    table.append(body);
    wrap.replaceChildren(h("div", { class: `table-wrap${maxHeight ? " table-scroll" : ""}` }, table));
    if (pageSize && rows.length > pageSize) {
      const pages = Math.ceil(rows.length / pageSize);
      wrap.append(h("div", { class: "pager" },
        h("span", {}, `Rows ${page * pageSize + 1}–${Math.min((page + 1) * pageSize, rows.length)} of ${rows.length}`),
        h("span", {},
          h("button", { class: "btn btn-quiet", type: "button", disabled: page === 0 || null, onclick: () => { page--; render(); } }, "Previous"),
          h("button", { class: "btn btn-quiet", type: "button", disabled: page >= pages - 1 || null, onclick: () => { page++; render(); } }, "Next"))));
    }
  };
  render();
  return wrap;
}

export function deltaBadge(trend, text, label) {
  const cls = trend === "up" ? "delta-up" : trend === "down" ? "delta-down" : "delta-flat";
  const icon = trend === "up" ? icons.up : trend === "down" ? icons.down : icons.flat;
  const word = trend === "up" ? "Up" : trend === "down" ? "Down" : "No change";
  return `<span class="delta ${cls}"><span class="visually-hidden">${word} </span>${icon}${escapeHtml(text)}</span>${label ? `<span>${escapeHtml(label)}</span>` : ""}`;
}

/**
 * KPI ledger. kpis: API objects; series: monthly rows for sparklines; selected: indexes of
 * months inside the selected period; yoy: {current, previous} revenue a year earlier.
 */
export function kpiStrip({ kpis, series, selectedIdx, compareLabel, yoy, format }) {
  const el = h("div", { class: "kpis enter", role: "list" });
  const sparkKey = { revenue: "revenue", estimated_profit: "estimated_profit", estimated_margin: "estimated_margin",
    orders: "orders", customers: "customers", avg_order_value: "avg_order_value" };
  for (const k of kpis) {
    const isRatio = k.unit === "ratio";
    const change = isRatio ? pts(k.change_abs) : signedPct(k.change_pct === null ? null : k.change_pct / 100);
    const values = series.map((r) => r[sparkKey[k.metric]]);
    el.append(h("div", { class: "kpi", role: "listitem", html: `
      <div class="kpi-label">${escapeHtml(k.label.replace(" (est.)", ""))}${k.is_estimated
        ? '<span class="est" title="Estimated with a synthetic cost model: Olist does not publish product costs.">est.</span>' : ""}</div>
      <div class="kpi-value">${escapeHtml(format(k))}</div>
      <div class="kpi-delta">${deltaBadge(k.trend, change, `vs ${compareLabel}`)}</div>
      <div class="kpi-spark" title="Last 12 complete months">${sparkline(values, selectedIdx)}</div>` }));
  }
  // Growth: year-over-year revenue, when the same period a year earlier is inside the data window.
  const g = yoy && yoy.previous ? yoy.current / yoy.previous - 1 : null;
  const trend = g === null ? "n/a" : Math.abs(g) < 0.005 ? "flat" : g > 0 ? "up" : "down";
  el.append(h("div", { class: "kpi", role: "listitem", html: `
    <div class="kpi-label">Growth (year over year)</div>
    <div class="kpi-value">${g === null ? "–" : escapeHtml(signedPct(g))}</div>
    <div class="kpi-delta">${g === null
      ? "<span>No data for the same period a year earlier</span>"
      : deltaBadge(trend, "Revenue", `vs ${escapeHtml(yoy.label)}`)}</div>
    <div class="kpi-spark"></div>` }));
  el.addEventListener("animationend", () => el.classList.remove("enter"), { once: true });
  return el;
}

export function insightList(items) {
  if (!items.length) return h("p", { class: "empty" }, "Not enough data in this period to draw conclusions.");
  const list = h("div", { class: "insights" });
  for (const i of items) {
    list.append(h("article", { class: `insight insight-${i.tone}`, html: `
      ${icons[i.tone]}<h3>${escapeHtml(i.title)}</h3><p>${escapeHtml(i.text)}</p>` }));
  }
  return list;
}

export function errorBox(error) {
  const hint = error.status === 0
    ? "Start the API with <code>python -m uvicorn backend.main:app --reload</code> and refresh."
    : escapeHtml(error.message);
  return h("div", { class: "error-box", role: "alert", html: `<p><strong>Could not load this section.</strong></p><p>${hint}</p>` });
}

export function monthIndexesInRange(series, start, end) {
  return series.map((r, i) => (r.month >= start.slice(0, 7) + "-01" && r.month <= end ? i : -1)).filter((i) => i >= 0);
}

export { monthLabel };
