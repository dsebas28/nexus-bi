import * as api from "../api.js";
import { barMarker, plot, theme } from "../charts.js";
import { dataTable, h, panel } from "../components.js";
import { num } from "../format.js";

const ACTIONS = { removed: "Removed", corrected: "Corrected", imputed: "Imputed", nulled: "Set to empty", flagged: "Flagged", kept: "Kept" };

export default {
  id: "quality",
  filters: [],
  group: "Data",
  title: "Data quality",
  subtitle: "What the pipeline found in the raw data, and what it did about it",

  async render(root) {
    const dq = await api.get("/data-quality");
    const t = theme();
    const finished = new Date(dq.finished_at);

    const stat = (label, value) => h("div", {}, h("div", { class: "stat-label" }, label), h("div", { class: "stat-value num" }, num(value)));
    root.append(panel({
      title: "Data quality report",
      sub: `Pipeline run ${dq.run_id}, finished ${finished.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}. ` +
        `${dq.checks_run} checks ran; ${dq.checks_with_findings} found something.`,
      body: h("div", { class: "stats" },
        stat("Rows processed", dq.rows_processed), stat("Duplicates removed", dq.duplicates_removed),
        stat("Missing values handled", dq.missing_values_handled), stat("Invalid dates", dq.invalid_dates),
        stat("Outliers detected", dq.outliers_detected), stat("Format errors corrected", dq.format_errors_corrected),
        stat("Inconsistencies flagged", dq.inconsistencies_flagged), stat("Final records", dq.final_records)),
    }).root);

    const byCategory = Object.entries(dq.issues.reduce((acc, i) => ((acc[i.category] = (acc[i.category] || 0) + i.rows_affected), acc), {}))
      .map(([category, rows]) => ({ category, rows })).sort((a, b) => a.rows - b.rows);
    const chartPanel = panel({ title: "Rows affected by type of problem", sub: "Log scale: duplicates in the geolocation file dwarf everything else",
      chartClass: "chart chart-sm", table: () => ({ columns: [{ key: "category", label: "Type" }, { key: "rows", label: "Rows", align: "r", format: (v) => num(v) }], rows: byCategory }) });
    root.append(h("div", { class: "grid grid-main" },
      panel({ title: "Every check with findings", sub: "Sort by any column",
        body: dataTable({ rows: dq.issues, pageSize: 12, maxHeight: false, columns: [
          { key: "source_table", label: "Table" },
          { key: "check_name", label: "Check", format: (v) => v.replace(/_/g, " ") },
          { key: "category", label: "Type" },
          { key: "rows_affected", label: "Rows", align: "r", format: (v) => num(v) },
          { key: "action_taken", label: "Action", format: (v) => ACTIONS[v] || v },
        ] }) }).root,
      chartPanel.root));

    requestAnimationFrame(() => {
      plot(chartPanel.chart, [{ type: "bar", orientation: "h", x: byCategory.map((r) => r.rows), y: byCategory.map((r) => r.category.replace("_", " ")),
        marker: barMarker(t.series[0]), hovertemplate: "%{y}: %{x:,} rows<extra></extra>" }],
        { xaxis: { type: "log", showgrid: true }, yaxis: { showgrid: false } });
    });
  },
};
