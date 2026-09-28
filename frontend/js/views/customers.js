import * as api from "../api.js";
import { barMarker, plot, sequentialScale, theme } from "../charts.js";
import { h, panel } from "../components.js";
import { brl, monthLabel, num, pct } from "../format.js";

export default {
  id: "customers",
  filters: ["period", "state"],
  group: "Analytics",
  title: "Customers",
  subtitle: "New and returning buyers, purchase frequency, spend and lifetime value",

  async render(root, { params }) {
    const [ov, monthly, cohorts] = await Promise.all([
      api.get("/customers/overview", params),
      api.get("/customers/monthly", params),
      api.get("/customers/cohorts", { max_months: 6 }),
    ]);
    const t = theme();
    const b = ov.base;

    const stat = (label, value, note) => h("div", {}, h("div", { class: "stat-label" }, label),
      h("div", { class: "stat-value num" }, value), note ? h("div", { class: "stat-note" }, note) : null);
    root.append(panel({ title: "Customers in the selected period", body: h("div", { class: "stats" },
      stat("Active customers", num(ov.active_customers)),
      stat("New customers", num(ov.new_customers), `${pct(ov.new_customers / (ov.active_customers || 1))} of active`),
      stat("Returning customers", num(ov.returning_customers)),
      stat("Orders per customer", num(ov.avg_orders_per_customer, { decimals: 2 }), "in the period"),
      stat("Spend per customer", brl(ov.avg_spend_in_period, { decimals: 2 }), "in the period"),
    ) }).root);

    root.append(panel({ title: "Whole customer base", sub: "All customers up to the reference date. Lifetime value (CLV) is historical revenue per customer.",
      body: h("div", { class: "stats" },
        stat("Customers", num(b.customers)),
        stat("Bought more than once", pct(b.repeat_rate, 2), `${num(b.repeat_customers)} customers`),
        stat("Average CLV", brl(b.avg_clv_revenue, { decimals: 2 }), `profit (est.) ${brl(b.avg_clv_estimated_profit, { decimals: 2 })}`),
        stat("CLV of repeat buyers", brl(b.avg_clv_repeat, { decimals: 2 }), `vs ${brl(b.avg_clv_one_time, { decimals: 2 })} one-time`),
        stat("Median days between orders", num(b.median_days_between_orders), "repeat buyers"),
      ) }).root);

    const monthlyPanel = panel({ title: "New vs returning customers by month", sub: "Complete months",
      table: () => ({ columns: [
        { key: "month", label: "Month", format: monthLabel },
        { key: "new_customers", label: "New", align: "r", format: (v) => num(v) },
        { key: "returning_customers", label: "Returning", align: "r", format: (v) => num(v) },
        { key: "returning_revenue_share", label: "Revenue from returning", align: "r", format: (v) => pct(v) },
      ], rows: monthly }) });
    const freqPanel = panel({ title: "Orders per customer", sub: "Share of customers by number of orders (log scale)", chartClass: "chart chart-sm",
      table: () => ({ columns: [
        { key: "orders_bucket", label: "Orders", format: (v) => (v >= 5 ? "5+" : String(v)) },
        { key: "customers", label: "Customers", align: "r", format: (v) => num(v) },
        { key: "revenue", label: "Revenue", align: "r", format: (v) => brl(v) },
      ], rows: ov.frequency_distribution }) });
    root.append(h("div", { class: "grid grid-main" }, monthlyPanel.root, freqPanel.root));

    const cohortMonths = [...new Set(cohorts.map((c) => c.cohort_month))];
    const cohortPanel = panel({ title: "Cohort retention",
      sub: `Share of each month's new customers who bought again N months later. The highest cell is ${pct(Math.max(...cohorts.map((c) => c.retention)), 2)}.`,
      table: () => ({ columns: [
        { key: "cohort_month", label: "Cohort", format: monthLabel },
        { key: "cohort_customers", label: "Customers", align: "r", format: (v) => num(v) },
        { key: "month_offset", label: "Months later", align: "r" },
        { key: "retention", label: "Bought again", align: "r", format: (v) => pct(v, 2) },
      ], rows: cohorts }) });
    root.append(cohortPanel.root);

    requestAnimationFrame(() => {
      const x = monthly.map((m) => m.month);
      plot(monthlyPanel.chart, [
        { type: "bar", name: "New", x, y: monthly.map((m) => m.new_customers), marker: barMarker(t.series[0]),
          hovertemplate: "%{x|%b %Y}<br>%{y:,} new<extra></extra>" },
        { type: "bar", name: "Returning", x, y: monthly.map((m) => m.returning_customers), marker: barMarker(t.series[1]),
          hovertemplate: "%{x|%b %Y}<br>%{y:,} returning<extra></extra>" },
      ], { barmode: "stack", xaxis: { tickformat: "%b\n%Y", dtick: "M3" } });

      const total = ov.frequency_distribution.reduce((s, r) => s + r.customers, 0);
      plot(freqPanel.chart, [{ type: "bar", x: ov.frequency_distribution.map((r) => (r.orders_bucket >= 5 ? "5+" : String(r.orders_bucket))),
        y: ov.frequency_distribution.map((r) => (r.customers / total) * 100), marker: barMarker(t.series[0]),
        customdata: ov.frequency_distribution.map((r) => r.customers),
        hovertemplate: "%{x} order(s)<br>%{y:.3f}% of customers (%{customdata:,})<extra></extra>" }],
        { yaxis: { type: "log", ticksuffix: "%", title: { text: "% of customers" } }, xaxis: { title: { text: "Orders" }, type: "category" } });

      const offsets = [1, 2, 3, 4, 5, 6];
      const z = cohortMonths.map((cm) => offsets.map((o) => {
        const cell = cohorts.find((c) => c.cohort_month === cm && c.month_offset === o);
        return cell ? cell.retention * 100 : null;
      }));
      plot(cohortPanel.chart, [{ type: "heatmap", z, x: offsets.map((o) => `+${o} mo`), y: cohortMonths.map(monthLabel),
        colorscale: sequentialScale(t), xgap: 2, ygap: 2, hoverongaps: false,
        colorbar: { thickness: 10, outlinewidth: 0, ticksuffix: "%", tickfont: { color: t.ink3 } },
        hovertemplate: "%{y} cohort, %{x}<br>%{z:.2f}% bought again<extra></extra>" }],
        { yaxis: { autorange: "reversed", showgrid: false } });
    });
  },
};
