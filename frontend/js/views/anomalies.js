import * as api from "../api.js";
import { plot, theme } from "../charts.js";
import { dataTable, h, panel } from "../components.js";
import { brl, dayLabel, num } from "../format.js";

let type = "day";
const TYPES = [["day", "Days"], ["product", "Products"], ["customer", "Customers"]];
const METHODS = {
  robust_z_same_weekday: "Same-weekday pattern",
  isolation_forest: "Isolation Forest",
  poisson_vs_own_history: "Own sales history",
};

export default {
  id: "anomalies",
  filters: [],
  group: "Machine learning",
  title: "Anomalies",
  subtitle: "Days, products and customers that broke their usual pattern",

  async render(root, { meta }) {
    const p = meta.reporting_period;
    const [all, daily] = await Promise.all([
      api.get("/anomalies", { limit: 500 }),
      api.get("/sales/daily", { start: p.first_month, end: p.data_end }),
    ]);
    const t = theme();
    const count = (et) => all.counts.filter((c) => c.entity_type === et).reduce((s, c) => s + c.anomalies, 0);
    const dayRevenue = all.anomalies.filter((a) => a.entity_type === "day" && a.metric === "revenue");

    const chartPanel = panel({
      title: "Daily revenue with unusual days marked",
      sub: "Each day is compared with the same weekday at the current sales level. Hover a marker for the explanation.",
      chartClass: "chart chart-lg",
      table: () => ({ columns: [{ key: "period_start", label: "Day", format: dayLabel }, { key: "direction", label: "Direction" },
        { key: "observed", label: "Revenue", align: "r", format: (v) => brl(v) }, { key: "expected", label: "Expected", align: "r", format: (v) => brl(v) },
        { key: "score", label: "Score", align: "r" }], rows: dayRevenue }),
    });
    root.append(chartPanel.root);

    const toggle = h("div", { class: "segmented", role: "group", "aria-label": "Anomaly type" },
      ...TYPES.map(([id, label]) => h("button", { type: "button", "aria-pressed": String(id === type),
        onclick: () => { type = id; document.dispatchEvent(new CustomEvent("nexus:rerender")); } }, `${label} (${count(id)})`)));
    const rows = all.anomalies.filter((a) => a.entity_type === type);
    root.append(panel({
      title: "Detected anomalies", sub: "Strongest first. Each one is a signal to investigate, with the numbers behind it.",
      actions: [toggle],
      body: dataTable({ rows, pageSize: 12, maxHeight: false, columns: [
        { key: "period_start", label: type === "day" ? "Day" : "From", format: dayLabel },
        ...(type === "day" ? [] : [{ key: "entity_label", label: type === "product" ? "Product" : "Customer" }]),
        { key: "direction", label: "Direction", format: (v) => h("span", { class: `chip ${v === "down" ? "chip-high" : v === "up" ? "chip-brand" : ""}` }, v) },
        { key: "method", label: "Method", format: (v) => METHODS[v] || v },
        { key: "score", label: "Score", align: "r", format: (v) => num(v, { decimals: 1 }) },
        { key: "description", label: "Explanation", sort: false },
      ] }),
    }).root);

    requestAnimationFrame(() => {
      const marks = (dir, color, symbol, name) => {
        const pts = dayRevenue.filter((a) => a.direction === dir);
        return { type: "scatter", mode: "markers", name, x: pts.map((a) => a.period_start), y: pts.map((a) => a.observed),
          marker: { color, size: 11, symbol, line: { color: t.surface, width: 2 } }, text: pts.map((a) => a.description),
          hovertemplate: "%{text}<extra></extra>" };
      };
      plot(chartPanel.chart, [
        { type: "scatter", mode: "lines", name: "Daily revenue", x: daily.map((d) => d.day), y: daily.map((d) => d.revenue),
          line: { color: t.muted, width: 1.4 }, hovertemplate: "%{x|%a %d %b %Y}<br>R$ %{y:,.0f}<extra></extra>" },
        marks("up", t.series[0], "triangle-up", "Unusual spike"),
        marks("down", t.series[7], "triangle-down", "Unexpected drop"),
      ], { yaxis: { tickprefix: "R$ ", tickformat: "~s" }, hovermode: "closest" });
    });
  },
};
