import * as api from "../api.js";
import { barMarker, plot, theme } from "../charts.js";
import { dataTable, h, panel } from "../components.js";
import { brl, category, dayLabel, num, pct } from "../format.js";

const SORTS = [
  { id: "revenue", label: "Revenue", fmt: (v) => brl(v), tick: { tickprefix: "R$ ", tickformat: "~s" } },
  { id: "units", label: "Units", fmt: (v) => num(v), tick: {} },
  { id: "estimated_profit", label: "Profit (est.)", fmt: (v) => brl(v), tick: { tickprefix: "R$ ", tickformat: "~s" } },
  { id: "estimated_margin", label: "Margin (est.)", fmt: (v) => pct(v), tick: { tickformat: ".0%" } },
];
let sortBy = "revenue";

const productCols = [
  { key: "product_short_id", label: "Product" },
  { key: "category", label: "Category", format: category },
  { key: "units", label: "Units", align: "r", format: (v) => num(v) },
  { key: "revenue", label: "Revenue", align: "r", format: (v) => brl(v) },
  { key: "estimated_margin", label: "Margin (est.)", align: "r", format: (v) => pct(v) },
];

export default {
  id: "products",
  group: "Analytics",
  title: "Products",
  subtitle: "Best sellers, most profitable, and products that stopped selling. Olist anonymises product names.",

  async render(root, { params }) {
    const sort = SORTS.find((s) => s.id === sortBy);
    const minUnits = sortBy === "estimated_margin" ? 10 : 1;
    const [top, low, hrlm] = await Promise.all([
      api.get("/products/top", { ...params, sort_by: sortBy, limit: 25, min_units: minUnits }),
      api.get("/products/low-rotation", { category: params.category, limit: 25 }),
      api.get("/products/high-revenue-low-margin", { limit: 25 }),
    ]);
    const t = theme();

    const toggle = h("div", { class: "segmented", role: "group", "aria-label": "Rank products by" },
      ...SORTS.map((s) => h("button", { type: "button", "aria-pressed": String(s.id === sortBy),
        onclick: () => { sortBy = s.id; document.dispatchEvent(new CustomEvent("nexus:rerender")); } }, s.label)));

    const topPanel = panel({
      title: `Top products by ${sort.label.toLowerCase()}`,
      sub: sortBy === "estimated_margin" ? "Products with at least 10 units in the period" : "Selected period and filters",
      actions: [toggle],
      table: () => ({ columns: [...productCols.slice(0, 3), { key: "orders", label: "Orders", align: "r" }, ...productCols.slice(3),
        { key: "avg_price", label: "Avg price", align: "r", format: (v) => brl(v, { decimals: 2 }) }], rows: top.rows }),
    });
    root.append(topPanel.root);

    root.append(h("div", { class: "grid grid-2" },
      panel({ title: "Products that stopped selling", sub: low.criteria,
        body: dataTable({ rows: low.rows, pageSize: 10, maxHeight: false, columns: [...productCols.slice(0, 4),
          { key: "last_sale", label: "Last sale", format: dayLabel },
          { key: "days_since_last_sale", label: "Days since", align: "r" }] }) }).root,
      panel({ title: "High revenue, low margin", sub: hrlm.criteria,
        body: dataTable({ rows: hrlm.rows, pageSize: 10, maxHeight: false, columns: productCols }) }).root));

    requestAnimationFrame(() => {
      const rows = top.rows.slice(0, 15).reverse();
      plot(topPanel.chart, [{ type: "bar", orientation: "h", x: rows.map((r) => r[sortBy]),
        y: rows.map((r) => `${r.product_short_id} (${category(r.category)})`), marker: barMarker(t.series[0]),
        customdata: rows.map((r) => [r.units, brl(r.revenue), pct(r.estimated_margin)]),
        hovertemplate: "%{y}<br>%{customdata[0]} units, %{customdata[1]}, margin (est.) %{customdata[2]}<extra></extra>" }],
        { xaxis: { ...sort.tick, showgrid: true }, yaxis: { showgrid: false }, height: 460 });
    });
  },
};
