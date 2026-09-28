import * as api from "../api.js";
import { barMarker, plot, theme } from "../charts.js";
import { dataTable, h, panel } from "../components.js";
import { brl, city, num, pct } from "../format.js";

let selected = "Potential";

export default {
  id: "segments",
  filters: [],
  group: "Machine learning",
  title: "Customer segments",
  subtitle: "RFM segmentation with thresholds taken from how customers actually return",

  async render(root) {
    const seg = await api.get("/segments");
    const customers = await api.get(`/segments/${encodeURIComponent(selected)}/customers`, { limit: 200 });
    const t = theme();
    const gap = seg.thresholds.repurchase_gap_days;

    root.append(panel({ title: "How the segments are defined", body: h("p", { class: "panel-sub", html:
      `Recency is judged against the real repurchase cycle: 75% of repeat purchases happen within <b>${Math.round(gap.p75)} days</b> ` +
      `and 90% within <b>${Math.round(gap.p90)} days</b> (${num(gap.n_gaps)} observed returns). Beyond those points a customer is ` +
      "<b>At Risk</b> and then <b>Lost</b>. Among active customers, repeat buyers are <b>Loyal</b>, and those in the top fifth of spend " +
      "are <b>VIP</b>. Recent one-time buyers are <b>Potential</b>. Rules are applied in that order." }) }).root);

    const tiles = h("div", { class: "segments", role: "group", "aria-label": "Segments" },
      ...seg.segments.map((s) => h("button", { class: "segment", type: "button", "aria-pressed": String(s.segment === selected),
        onclick: () => { selected = s.segment; document.dispatchEvent(new CustomEvent("nexus:rerender")); } },
        h("span", { class: "segment-name" }, s.segment),
        h("span", { class: "segment-figs" },
          h("div", {}, h("b", { class: "num" }, pct(s.customer_share)), h("span", {}, `${num(s.customers)} customers`)),
          h("div", {}, h("b", { class: "num" }, pct(s.revenue_share)), h("span", {}, "of revenue"))),
        h("span", { class: "segment-rule" }, s.rule))));
    root.append(tiles);

    const current = seg.segments.find((s) => s.segment === selected);
    const detail = panel({ title: `${current.segment}: ${num(current.customers)} customers`, sub: current.description,
      body: h("div", { style: "display:flex; flex-direction:column; gap:16px" },
        h("div", { class: "stats" },
          ...[["Avg lifetime revenue", brl(current.avg_revenue_per_customer, { decimals: 2 })],
            ["Avg orders", num(current.avg_orders, { decimals: 2 })],
            ["Avg days since last purchase", num(current.avg_recency_days)],
            ["Revenue", brl(current.revenue)]].map(([l, v]) => h("div", {}, h("div", { class: "stat-label" }, l), h("div", { class: "stat-value num" }, v)))),
        h("p", { class: "recommendation" }, h("b", {}, "Recommended action: "), current.recommendation),
        dataTable({ rows: customers, pageSize: 10, maxHeight: false, caption: `Customers in ${selected}, highest spend first`, columns: [
          { key: "customer_short_id", label: "Customer" },
          { key: "city", label: "City", format: (v, r) => `${city(v)} (${r.state_code})` },
          { key: "frequency", label: "Orders", align: "r" },
          { key: "monetary", label: "Lifetime revenue", align: "r", format: (v) => brl(v, { decimals: 2 }) },
          { key: "recency_days", label: "Days since last order", align: "r" },
          { key: "rfm_code", label: "RFM score", align: "r" },
        ] })) });

    const share = panel({ title: "Share of customers vs share of revenue", chartClass: "chart chart-sm",
      table: () => ({ columns: [{ key: "segment", label: "Segment" },
        { key: "customer_share", label: "Customers", align: "r", format: (v) => pct(v) },
        { key: "revenue_share", label: "Revenue", align: "r", format: (v) => pct(v) }], rows: seg.segments }) });
    root.append(h("div", { class: "grid grid-main" }, detail.root, share.root));

    requestAnimationFrame(() => {
      const x = seg.segments.map((s) => s.segment);
      plot(share.chart, [
        { type: "bar", name: "% of customers", x, y: seg.segments.map((s) => s.customer_share), marker: barMarker(t.series[0]),
          hovertemplate: "%{x}: %{y:.1%} of customers<extra></extra>" },
        { type: "bar", name: "% of revenue", x, y: seg.segments.map((s) => s.revenue_share), marker: barMarker(t.series[1]),
          hovertemplate: "%{x}: %{y:.1%} of revenue<extra></extra>" },
      ], { barmode: "group", bargroupgap: 0.08, yaxis: { tickformat: ".0%" } });
    });
  },
};
