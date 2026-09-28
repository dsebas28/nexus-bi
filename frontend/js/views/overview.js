import * as api from "../api.js";
import { barMarker, plot, theme } from "../charts.js";
import { h, insightList, kpiStrip, panel } from "../components.js";
import { brl, category, monthLabel, pct, signedPct } from "../format.js";
import { compareLabel, formatKpi, recentMonths, yearOverYear } from "./shared.js";

export default {
  id: "overview",
  group: "Analytics",
  title: "Overview",
  subtitle: "How the business performed in the selected period, and what changed",

  async render(root, { filters, params, meta }) {
    const [kpis, monthly, yoy, insights, cats, geo] = await Promise.all([
      api.get("/kpis", params),
      api.get("/sales/monthly", { state: params.state, category: params.category }),
      yearOverYear(filters, params, meta),
      api.get("/insights", params),
      api.get("/sales/categories", params),
      api.get("/geo/states", params),
    ]);
    const t = theme();
    const { series, selectedIdx } = recentMonths(monthly, filters);

    root.append(kpiStrip({ kpis: kpis.kpis, series, selectedIdx, compareLabel: compareLabel(filters), yoy, format: formatKpi }));

    // Monthly revenue: bars in the selected period are emphasised, the rest recede.
    const complete = monthly.filter((m) => m.is_complete);
    const inPeriod = (m) => m.month >= filters.start.slice(0, 8) + "01" && m.month <= filters.end;
    const trend = panel({
      title: "Revenue by month",
      sub: "Complete months only. The selected period is highlighted; the line is estimated profit.",
      table: () => ({ columns: [
        { key: "month", label: "Month", format: monthLabel },
        { key: "revenue", label: "Revenue", align: "r", format: (v) => brl(v) },
        { key: "estimated_profit", label: "Profit (est.)", align: "r", format: (v) => brl(v) },
        { key: "orders", label: "Orders", align: "r" },
        { key: "revenue_growth_mom", label: "vs previous month", align: "r", format: (v) => signedPct(v) },
      ], rows: complete }),
    });
    const insightsPanel = panel({ title: "What changed", sub: "Computed from the data for the selected period and filters",
      body: insightList(insights.insights) });
    root.append(h("div", { class: "grid grid-main" }, trend.root, insightsPanel.root));

    // Where revenue came from.
    const topCats = cats.rows.slice(0, 8).reverse();
    const catPanel = panel({
      title: "Top categories", sub: "Revenue in the selected period",
      table: () => ({ columns: [
        { key: "category", label: "Category", format: category },
        { key: "revenue", label: "Revenue", align: "r", format: (v) => brl(v) },
        { key: "revenue_share", label: "Share", align: "r", format: (v) => pct(v) },
        { key: "revenue_growth", label: "vs previous", align: "r", format: (v) => signedPct(v) },
      ], rows: cats.rows }),
    });
    const topStates = geo.rows.filter((r) => r.revenue > 0).slice(0, 8).reverse();
    const statePanel = panel({
      title: "Top states", sub: "Revenue in the selected period",
      table: () => ({ columns: [
        { key: "state_name", label: "State" },
        { key: "revenue", label: "Revenue", align: "r", format: (v) => brl(v) },
        { key: "revenue_growth", label: "vs previous", align: "r", format: (v) => signedPct(v) },
      ], rows: geo.rows }),
    });
    root.append(h("div", { class: "grid grid-2" }, catPanel.root, statePanel.root));

    // Charts are drawn after insertion so Plotly can measure their containers.
    requestAnimationFrame(() => {
      plot(trend.chart, [
        // Two bar traces so the legend explains the emphasis (same bars, split by selection).
        ...[[true, "Revenue, selected period", t.series[0]], [false, "Revenue, other months", t.muted]].map(([sel, name, color]) => {
          const rows = complete.filter((m) => inPeriod(m) === sel);
          // Explicit width (~20 days): a one-bar trace on a date axis would otherwise be drawn 1 ms wide.
          return { type: "bar", name, x: rows.map((m) => m.month), y: rows.map((m) => m.revenue), marker: barMarker(color), width: 20 * 864e5,
            hovertemplate: "%{x|%b %Y}<br>Revenue R$ %{y:,.0f}<extra></extra>" };
        }),
        { type: "scatter", mode: "lines", name: "Profit (est.)", x: complete.map((m) => m.month),
          y: complete.map((m) => m.estimated_profit), line: { color: t.series[1], width: 2 },
          hovertemplate: "%{x|%b %Y}<br>Profit (est.) R$ %{y:,.0f}<extra></extra>" },
      ], { barmode: "overlay", yaxis: { tickprefix: "R$ ", tickformat: "~s" }, xaxis: { tickformat: "%b\n%Y", dtick: "M3" } });

      const hbar = (el, rows, label, key) => plot(el, [{
        type: "bar", orientation: "h", x: rows.map((r) => r.revenue), y: rows.map((r) => label(r)),
        marker: barMarker(t.series[0]), customdata: rows.map((r) => r.revenue_growth),
        hovertemplate: "%{y}<br>R$ %{x:,.0f}<br>vs previous %{customdata:+.1%}<extra></extra>",
      }], { xaxis: { tickprefix: "R$ ", tickformat: "~s", showgrid: true }, yaxis: { showgrid: false }, margin: { l: 8 } });
      hbar(catPanel.chart, topCats, (r) => category(r.category));
      hbar(statePanel.chart, topStates, (r) => r.state_name);
    });
  },
};
