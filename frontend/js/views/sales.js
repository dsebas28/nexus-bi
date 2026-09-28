import * as api from "../api.js";
import { barMarker, plot, sequentialScale, theme } from "../charts.js";
import { dataTable, h, panel } from "../components.js";
import { brl, category, dayLabel, monthLabel, num, pct, rangeLabel, signedPct } from "../format.js";

export default {
  id: "sales",
  group: "Analytics",
  title: "Sales",
  subtitle: "Revenue, orders, ticket size, categories and buying patterns",

  async render(root, { filters, params }) {
    const prevParams = { ...params, start: filters.previous_start, end: filters.previous_end };
    const [monthly, daily, prevDaily, cats, heat] = await Promise.all([
      api.get("/sales/monthly", { state: params.state, category: params.category }),
      api.get("/sales/daily", params),
      api.get("/sales/daily", prevParams),
      api.get("/sales/categories", params),
      api.get("/sales/heatmap", params),
    ]);
    const t = theme();
    const months = monthly.filter((m) => m.is_complete);
    const monthCols = [
      { key: "month", label: "Month", format: monthLabel },
      { key: "revenue", label: "Revenue", align: "r", format: (v) => brl(v) },
      { key: "estimated_profit", label: "Profit (est.)", align: "r", format: (v) => brl(v) },
      { key: "estimated_margin", label: "Margin (est.)", align: "r", format: (v) => pct(v) },
      { key: "orders", label: "Orders", align: "r", format: (v) => num(v) },
      { key: "avg_order_value", label: "Avg order", align: "r", format: (v) => brl(v, { decimals: 2 }) },
      { key: "revenue_growth_mom", label: "Growth vs previous month", align: "r", format: (v) => signedPct(v) },
    ];

    // Daily revenue in the period vs the comparison period, aligned by day number.
    const dailyPanel = panel({
      title: "Daily revenue", sub: `${rangeLabel(filters.start, filters.end)} compared with ${rangeLabel(filters.previous_start, filters.previous_end)}, aligned by day`,
      table: () => ({ columns: [
        { key: "day", label: "Day", format: dayLabel },
        { key: "revenue", label: "Revenue", align: "r", format: (v) => brl(v) },
        { key: "orders", label: "Orders", align: "r" },
      ], rows: daily }),
    });
    root.append(dailyPanel.root);

    const revPanel = panel({ title: "Revenue and estimated profit by month", sub: "Complete months", table: () => ({ columns: monthCols, rows: months }) });
    const ordersPanel = panel({ title: "Orders by month", chartClass: "chart chart-sm", table: () => ({ columns: monthCols, rows: months }) });
    const aovPanel = panel({ title: "Average order value by month", sub: "Revenue divided by orders", chartClass: "chart chart-sm",
      table: () => ({ columns: monthCols, rows: months }) });
    root.append(revPanel.root, h("div", { class: "grid grid-2" }, ordersPanel.root, aovPanel.root));

    const catPanel = panel({
      title: "Categories", sub: `Selected period vs ${rangeLabel(filters.previous_start, filters.previous_end)}. Sort any column.`,
      body: dataTable({ pageSize: 15, maxHeight: false, rows: cats.rows, columns: [
        { key: "category", label: "Category", format: category },
        { key: "revenue", label: "Revenue", align: "r", format: (v) => brl(v) },
        { key: "revenue_share", label: "Share", align: "r", format: (v) => pct(v) },
        { key: "revenue_growth", label: "Growth", align: "r", format: (v) => signedPct(v) },
        { key: "orders", label: "Orders", align: "r", format: (v) => num(v) },
        { key: "units", label: "Units", align: "r", format: (v) => num(v) },
        { key: "estimated_margin", label: "Margin (est.)", align: "r", format: (v) => pct(v) },
      ] }),
    });
    const heatPanel = panel({ title: "When customers buy", sub: "Orders by weekday and hour of purchase (Brasília time)",
      table: () => ({ columns: [{ key: "day_name", label: "Day" }, { key: "hour", label: "Hour", align: "r" },
        { key: "orders", label: "Orders", align: "r" }], rows: heat }) });
    root.append(h("div", { class: "grid grid-2" }, catPanel.root, heatPanel.root));

    requestAnimationFrame(() => {
      plot(dailyPanel.chart, [
        { type: "scatter", mode: "lines", name: rangeLabel(filters.previous_start, filters.previous_end),
          x: prevDaily.map((_, i) => i + 1), y: prevDaily.map((d) => d.revenue), line: { color: t.muted, width: 2 },
          customdata: prevDaily.map((d) => dayLabel(d.day)), hovertemplate: "%{customdata}<br>R$ %{y:,.0f}<extra></extra>" },
        { type: "scatter", mode: "lines", name: rangeLabel(filters.start, filters.end),
          x: daily.map((_, i) => i + 1), y: daily.map((d) => d.revenue), line: { color: t.series[0], width: 2 },
          customdata: daily.map((d) => dayLabel(d.day)), hovertemplate: "%{customdata}<br>R$ %{y:,.0f}<extra></extra>" },
      ], { hovermode: "x unified", xaxis: { title: { text: "Day of period" } }, yaxis: { tickprefix: "R$ ", tickformat: "~s" } });

      const x = months.map((m) => m.month);
      plot(revPanel.chart, [
        { type: "bar", name: "Revenue", x, y: months.map((m) => m.revenue), marker: barMarker(t.series[0]),
          hovertemplate: "%{x|%b %Y}<br>Revenue R$ %{y:,.0f}<extra></extra>" },
        { type: "bar", name: "Profit (est.)", x, y: months.map((m) => m.estimated_profit), marker: barMarker(t.series[1]),
          hovertemplate: "%{x|%b %Y}<br>Profit (est.) R$ %{y:,.0f}<extra></extra>" },
      ], { barmode: "group", bargroupgap: 0.08, yaxis: { tickprefix: "R$ ", tickformat: "~s" }, xaxis: { tickformat: "%b\n%Y", dtick: "M3" } });
      plot(ordersPanel.chart, [{ type: "bar", name: "Orders", x, y: months.map((m) => m.orders), marker: barMarker(t.series[0]),
        hovertemplate: "%{x|%b %Y}<br>%{y:,} orders<extra></extra>" }], { xaxis: { tickformat: "%b\n%Y", dtick: "M3" } });
      plot(aovPanel.chart, [{ type: "scatter", mode: "lines+markers", name: "Average order value", x,
        y: months.map((m) => m.avg_order_value), line: { color: t.series[0], width: 2 }, marker: { size: 8, color: t.series[0], line: { color: t.surface, width: 2 } },
        hovertemplate: "%{x|%b %Y}<br>R$ %{y:,.2f}<extra></extra>" }], { yaxis: { tickprefix: "R$ " }, xaxis: { tickformat: "%b\n%Y", dtick: "M3" } });

      const days = [...new Map(heat.map((c) => [c.day_of_week, c.day_name])).entries()].sort((a, b) => a[0] - b[0]);
      const z = days.map(([d]) => Array.from({ length: 24 }, (_, hr) => heat.find((c) => c.day_of_week === d && c.hour === hr)?.orders ?? 0));
      plot(heatPanel.chart, [{ type: "heatmap", z, x: Array.from({ length: 24 }, (_, i) => i), y: days.map(([, n]) => n),
        colorscale: sequentialScale(t), xgap: 2, ygap: 2, colorbar: { thickness: 10, outlinewidth: 0, tickfont: { color: t.ink3 } },
        hovertemplate: "%{y} %{x}:00<br>%{z:,} orders<extra></extra>" }],
        { yaxis: { autorange: "reversed", showgrid: false }, xaxis: { dtick: 3, title: { text: "Hour of day" } } });
    });
  },
};
