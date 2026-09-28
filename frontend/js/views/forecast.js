import * as api from "../api.js";
import { plot, theme } from "../charts.js";
import { dataTable, h, icons, panel } from "../components.js";
import { brl, dayLabel, num, pct } from "../format.js";

const DEFINITIONS = [
  ["MAE", "Average absolute error in R$ per day. Used to select the model."],
  ["RMSE", "Like MAE but penalises large misses more."],
  ["MAPE", "Average error as a percentage of the actual day."],
  ["R²", "Share of day-to-day variance explained. Daily revenue is noisy, so low values are expected."],
  ["Bias", "Total forecast vs total actual. Positive means the model over-forecasts."],
  ["Horizon total APE", "Error on the 8-week total, which is what planning usually needs."],
];

export default {
  id: "forecast",
  filters: [],
  group: "Machine learning",
  title: "Sales forecast",
  subtitle: "Daily revenue for the next 8 weeks, with an 80% range from the model's own past errors",

  async render(root) {
    const fc = await api.get("/forecast", { history_days: 150 });
    const t = theme();
    const model = fc.model;
    const actual = fc.series.filter((p) => p.kind === "actual");
    const future = fc.series.filter((p) => p.kind === "forecast");

    root.append(h("div", { class: "callout", role: "note", html: `${icons.warn}<span>${fc.disclaimer} In out-of-fold checks the 80% range contained
      <b>${pct(model.metrics.interval_coverage_out_of_fold, 0)}</b> of actual days.</span>` }));

    const chartPanel = panel({
      title: `Next 8 weeks: ${brl(fc.forecast_total)} expected`,
      sub: `Model: ${model.algorithm}, selected by the lowest backtest MAE. Shaded area: 80% range.`,
      chartClass: "chart chart-lg",
      table: () => ({ columns: [
        { key: "day", label: "Day", format: dayLabel },
        { key: "kind", label: "Type" },
        { key: "revenue", label: "Revenue", align: "r", format: (v) => brl(v) },
        { key: "lower_80", label: "80% low", align: "r", format: (v) => brl(v) },
        { key: "upper_80", label: "80% high", align: "r", format: (v) => brl(v) },
      ], rows: fc.series }),
    });
    root.append(chartPanel.root);

    // Model comparison from the rolling-origin backtest.
    const evals = model.evaluations.filter((e) => e.split === "rolling_backtest");
    const byCandidate = {};
    for (const e of evals) (byCandidate[e.candidate] ||= { candidate: e.candidate, selected: e.is_selected })[e.metric] = e.value;
    const rows = Object.values(byCandidate).sort((a, b) => a.MAE - b.MAE);
    const fixed = (d) => (v) => (v === null || v === undefined ? "–" : `${v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}`);
    root.append(h("div", { class: "grid grid-main" },
      panel({ title: "Model comparison", sub: `Rolling-origin backtest: ${model.params.backtest_folds} origins, each forecasting ${model.params.horizon_days} unseen days`,
        body: dataTable({ rows, maxHeight: false, columns: [
          { key: "candidate", label: "Model", format: (v, r) => (r.selected ? h("span", {}, v, " ", h("span", { class: "chip chip-brand" }, "selected")) : v) },
          { key: "MAE", label: "MAE (R$/day)", align: "r", format: (v) => num(v) },
          { key: "RMSE", label: "RMSE", align: "r", format: (v) => num(v) },
          { key: "MAPE", label: "MAPE %", align: "r", format: fixed(1) },
          { key: "R2", label: "R²", align: "r", format: fixed(2) },
          { key: "Bias %", label: "Bias %", align: "r", format: fixed(1) },
          { key: "Horizon total APE", label: "8-week total error %", align: "r", format: fixed(1) },
        ] }) }).root,
      panel({ title: "What the metrics mean", body: h("dl", { class: "definitions" },
        ...DEFINITIONS.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])) }).root));

    requestAnimationFrame(() => {
      plot(chartPanel.chart, [
        { type: "scatter", mode: "lines", x: [...future.map((p) => p.day), ...future.map((p) => p.day).reverse()],
          y: [...future.map((p) => p.upper_80), ...future.map((p) => p.lower_80).reverse()], fill: "toself",
          fillcolor: t.dark ? "rgba(57,135,229,0.18)" : "rgba(42,120,214,0.14)", line: { width: 0 }, name: "80% range", hoverinfo: "skip" },
        { type: "scatter", mode: "lines", name: "Actual", x: actual.map((p) => p.day), y: actual.map((p) => p.revenue),
          line: { color: t.ink3, width: 1.6 }, hovertemplate: "%{x|%a %d %b %Y}<br>Actual R$ %{y:,.0f}<extra></extra>" },
        { type: "scatter", mode: "lines", name: "Forecast", x: future.map((p) => p.day), y: future.map((p) => p.revenue),
          line: { color: t.series[0], width: 2 }, customdata: future.map((p) => [p.lower_80, p.upper_80]),
          hovertemplate: "%{x|%a %d %b %Y}<br>Forecast R$ %{y:,.0f}<br>80% range R$ %{customdata[0]:,.0f} to %{customdata[1]:,.0f}<extra></extra>" },
      ], { hovermode: "x unified", yaxis: { tickprefix: "R$ ", tickformat: "~s" },
        shapes: [{ type: "line", x0: future[0]?.day, x1: future[0]?.day, yref: "paper", y0: 0, y1: 1, line: { color: t.line, width: 1 } }] });
    });
  },
};
