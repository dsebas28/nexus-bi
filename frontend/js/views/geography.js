import * as api from "../api.js";
import { barMarker, divergingScale, plot, sequentialScale, theme } from "../charts.js";
import { dataTable, h, panel } from "../components.js";
import { brl, city, num, pct, rangeLabel, signedPct } from "../format.js";

const METRICS = [
  { id: "revenue", label: "Revenue", fmt: (v) => brl(v) },
  { id: "revenue_growth", label: "Growth", fmt: (v) => signedPct(v) },
  { id: "customers", label: "Customers", fmt: (v) => num(v) },
  { id: "estimated_margin", label: "Margin (est.)", fmt: (v) => pct(v) },
];
let metric = "revenue";

export default {
  id: "geography",
  group: "Analytics",
  title: "Geography",
  subtitle: "Where sales, customers, growth and profitability come from",

  async render(root, { filters, params }) {
    const [states, cities] = await Promise.all([
      api.get("/geo/states", params),
      api.get("/geo/cities", { ...params, limit: 400 }),
    ]);
    const t = theme();
    const m = METRICS.find((x) => x.id === metric);
    const toggle = h("div", { class: "segmented", role: "group", "aria-label": "Colour cities by" },
      ...METRICS.map((x) => h("button", { type: "button", "aria-pressed": String(x.id === metric),
        onclick: () => { metric = x.id; document.dispatchEvent(new CustomEvent("nexus:rerender")); } }, x.label)));

    const points = cities.rows.filter((r) => r.latitude !== null && (metric !== "revenue_growth" || r.revenue_previous > 0));
    const mapPanel = panel({
      title: `Cities by ${m.label.toLowerCase()}`,
      sub: `Top ${points.length} cities by revenue. Bubble size is revenue; colour is ${m.label.toLowerCase()}${
        metric === "revenue_growth" ? ` vs ${rangeLabel(filters.previous_start, filters.previous_end)} (blue up, red down)` : ""}.`,
      chartClass: "chart chart-lg", actions: [toggle],
      table: () => ({ columns: [
        { key: "city", label: "City", format: city }, { key: "state_code", label: "State" },
        { key: "revenue", label: "Revenue", align: "r", format: (v) => brl(v) },
        { key: "revenue_growth", label: "Growth", align: "r", format: (v) => signedPct(v) },
        { key: "customers", label: "Customers", align: "r", format: (v) => num(v) },
        { key: "estimated_margin", label: "Margin (est.)", align: "r", format: (v) => pct(v) },
      ], rows: cities.rows }),
    });
    root.append(mapPanel.root);

    const withSales = states.rows.filter((r) => r.revenue > 0);
    const growthPanel = panel({ title: "Revenue growth by state", sub: `vs ${rangeLabel(filters.previous_start, filters.previous_end)}; states with at least 30 orders`,
      table: () => ({ columns: [{ key: "state_name", label: "State" }, { key: "revenue_growth", label: "Growth", align: "r", format: (v) => signedPct(v) }],
        rows: withSales }) });
    const tablePanel = panel({ title: "States", sub: "Delivery and review figures cover all categories",
      body: dataTable({ rows: withSales, pageSize: 14, maxHeight: false, columns: [
        { key: "state_name", label: "State" },
        { key: "revenue", label: "Revenue", align: "r", format: (v) => brl(v) },
        { key: "revenue_growth", label: "Growth", align: "r", format: (v) => signedPct(v) },
        { key: "customers", label: "Customers", align: "r", format: (v) => num(v) },
        { key: "avg_order_value", label: "Avg order", align: "r", format: (v) => brl(v, { decimals: 2 }) },
        { key: "estimated_margin", label: "Margin (est.)", align: "r", format: (v) => pct(v) },
        { key: "avg_delivery_days", label: "Delivery days", align: "r", format: (v) => num(v, { decimals: 1 }) },
        { key: "late_delivery_rate", label: "Late", align: "r", format: (v) => pct(v) },
        { key: "avg_review_score", label: "Review", align: "r", format: (v) => (v === null ? "–" : `${v.toFixed(2)}★`) },
      ] }) });
    root.append(h("div", { class: "grid grid-main" }, tablePanel.root, growthPanel.root));

    requestAnimationFrame(() => {
      const values = points.map((r) => r[metric]);
      const maxRev = Math.max(...points.map((r) => r.revenue), 1);
      const isGrowth = metric === "revenue_growth";
      const cap = isGrowth ? Math.max(0.25, ...values.map((v) => Math.min(Math.abs(v), 1))) : undefined;
      // Magnitudes are heavily skewed (Sao Paulo dwarfs everything): cap the colour scale at the 95th percentile.
      const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
      const p95 = sorted[Math.floor(sorted.length * 0.95)] ?? undefined;
      plot(mapPanel.chart, [{
        type: "scattergeo", lat: points.map((r) => r.latitude), lon: points.map((r) => r.longitude),
        text: points.map((r) => `${city(r.city)} (${r.state_code})`),
        customdata: points.map((r) => [brl(r.revenue), m.fmt(r[metric]), num(r.customers)]),
        hovertemplate: "<b>%{text}</b><br>Revenue %{customdata[0]}<br>" + `${m.label} ` + "%{customdata[1]}<br>Customers %{customdata[2]}<extra></extra>",
        marker: {
          size: points.map((r) => 5 + 30 * Math.sqrt(r.revenue / maxRev)), sizemode: "diameter", opacity: 0.85,
          line: { color: t.surface, width: 1 },
          color: values, colorscale: isGrowth ? divergingScale(t) : sequentialScale(t),
          cmin: isGrowth ? -cap : undefined, cmax: isGrowth ? cap : p95, cmid: isGrowth ? 0 : undefined,
          colorbar: { thickness: 10, outlinewidth: 0, tickformat: isGrowth || metric === "estimated_margin" ? ".0%" : "~s",
            tickfont: { color: t.ink3 }, x: 1, xpad: 4 },
        },
      }], {
        geo: {
          scope: "south america", projection: { type: "mercator" }, resolution: 50,
          lonaxis: { range: [-75, -33] }, lataxis: { range: [-34, 6] },
          showland: true, landcolor: getComputedStyle(document.documentElement).getPropertyValue("--surface-sunken").trim(),
          showcountries: true, countrycolor: t.line, showcoastlines: true, coastlinecolor: t.line,
          showocean: false, showlakes: false, bgcolor: "rgba(0,0,0,0)", framewidth: 0,
        },
        margin: { l: 0, r: 0, t: 0, b: 0 }, dragmode: "pan",
      }, { scrollZoom: true });

      const g = withSales.filter((r) => r.revenue_growth !== null && r.orders >= 30).sort((a, b) => a.revenue_growth - b.revenue_growth);
      plot(growthPanel.chart, [{ type: "bar", orientation: "h", x: g.map((r) => r.revenue_growth), y: g.map((r) => r.state_code),
        marker: barMarker(g.map((r) => (r.revenue_growth >= 0 ? t.series[0] : t.series[7]))),
        customdata: g.map((r) => [r.state_name, brl(r.revenue_previous), brl(r.revenue)]),
        hovertemplate: "%{customdata[0]}<br>%{x:+.1%} (%{customdata[1]} to %{customdata[2]})<extra></extra>" }],
        { xaxis: { tickformat: "+.0%", showgrid: true, zeroline: true, zerolinecolor: t.line }, yaxis: { showgrid: false, dtick: 1 },
          height: Math.max(320, g.length * 20 + 40) });
    });
  },
};
