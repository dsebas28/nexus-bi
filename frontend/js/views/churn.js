import * as api from "../api.js";
import { barMarker, plot, theme } from "../charts.js";
import { dataTable, h, icons, panel } from "../components.js";
import { brl, city, num, pct } from "../format.js";

let band = "High";
let segment = "";

const FEATURE_LABELS = {
  recency_days: "Time since last purchase", frequency: "Number of purchases", tenure_days: "Time as a customer",
  monetary: "Lifetime spend", avg_order_value: "Average order value", avg_items_per_order: "Items per order",
  freight_ratio: "Freight share of order", max_installments: "Installments", late_share: "Share of late deliveries",
  avg_delivery_days: "Delivery time", avg_review: "Average review", has_review: "Left a review", last_review: "Last review",
  last_order_late: "Last order late", paid_boleto_share: "Paid by boleto", paid_voucher_share: "Paid by voucher",
  region_norte: "Region: Norte", region_nordeste: "Region: Nordeste", region_centro_oeste: "Region: Centro-Oeste",
  region_sudeste: "Region: Sudeste", region_sul: "Region: Sul",
};

export default {
  id: "churn",
  filters: ["state"],
  group: "Machine learning",
  title: "Churn risk",
  subtitle: "Probability that a customer will not buy again within 180 days",

  async render(root, { params }) {
    const [summary, customers] = await Promise.all([
      api.get("/churn/summary"),
      api.get("/churn/customers", { risk_band: band, segment, state: params.state, limit: 200 }),
    ]);
    const t = theme();
    const model = summary.model;
    const m = model.metrics;
    const ev = (candidate, split, metric) => model.evaluations.find((e) => e.candidate === candidate && e.split === split && e.metric === metric)?.value ?? null;
    const test = (metric) => ev(m.selected_model, "out_of_time_test", metric);
    const baseRate = m.test_return_rate;

    root.append(h("div", { class: "callout", role: "note", html: `${icons.warn}<span>Transactional history predicts churn only weakly here:
      ROC-AUC is <b>${test("roc_auc").toFixed(3)}</b> on a later period the model never saw (0.5 = random). Use the score to
      <b>rank and prioritise</b> customers for retention, not as a certainty about any single customer.</span>` }));

    const metricItem = (label, value, note) => h("div", {}, h("dt", {}, label), h("dd", { class: "num" }, value, h("small", {}, note)));
    root.append(panel({
      title: `Model: ${model.algorithm}`,
      sub: `Selected by cross-validated ROC-AUC. Trained on a snapshot at ${model.params.train_cutoff}, tested out-of-time on ${model.params.test_cutoff}.`,
      body: h("dl", { class: "metric-list" },
        metricItem("ROC-AUC (test)", test("roc_auc").toFixed(3), summary.metric_definitions.roc_auc),
        metricItem("PR-AUC, returning", test("pr_auc_returning").toFixed(3), `Base rate ${pct(baseRate, 2)}: a random model scores about that.`),
        metricItem("Precision, returning", pct(test("precision_returning")), summary.metric_definitions.precision),
        metricItem("Recall, returning", pct(test("recall_returning")), summary.metric_definitions.recall),
        metricItem("F1, returning", test("f1_returning").toFixed(3), summary.metric_definitions.f1),
        metricItem("Brier score", test("brier").toFixed(4), summary.metric_definitions.brier)),
    }).root);

    const candidates = [...new Set(model.evaluations.map((e) => e.candidate))];
    const compareRows = candidates.map((c) => ({ candidate: c, selected: c === m.selected_model,
      cv_auc: ev(c, "cv_train", "roc_auc"), test_auc: ev(c, "out_of_time_test", "roc_auc"),
      test_pr: ev(c, "out_of_time_test", "pr_auc_returning"), accuracy: ev(c, "out_of_time_test", "accuracy") }));
    const cm = { tp: test("tp"), fn: test("fn"), fp: test("fp"), tn: test("tn") };
    const cmPanel = panel({ title: "Confusion matrix (test period)", sub: "Churn is the positive class", chartClass: "chart chart-sm",
      table: () => ({ columns: [{ key: "a", label: "" }, { key: "p1", label: "Predicted churn", align: "r" }, { key: "p0", label: "Predicted return", align: "r" }],
        rows: [{ a: "Actually churned", p1: num(cm.tp), p0: num(cm.fn) }, { a: "Actually returned", p1: num(cm.fp), p0: num(cm.tn) }] }) });
    root.append(h("div", { class: "grid grid-2" },
      panel({ title: "Candidates and baselines", sub: "Accuracy is shown to make a point: predicting that everyone churns scores ~99% while finding nobody to retain.",
        body: dataTable({ rows: compareRows, maxHeight: false, columns: [
          { key: "candidate", label: "Model", format: (v, r) => (r.selected ? h("span", {}, v, " ", h("span", { class: "chip chip-brand" }, "selected")) : v) },
          { key: "cv_auc", label: "CV ROC-AUC", align: "r", format: (v) => (v === null ? "–" : v.toFixed(3)) },
          { key: "test_auc", label: "Test ROC-AUC", align: "r", format: (v) => (v === null ? "–" : v.toFixed(3)) },
          { key: "test_pr", label: "Test PR-AUC", align: "r", format: (v) => (v === null ? "–" : v.toFixed(3)) },
          { key: "accuracy", label: "Accuracy", align: "r", format: (v) => (v === null ? "–" : pct(v)) },
        ] }) }).root,
      cmPanel.root));

    const factorPanel = panel({ title: "Most common risk factors among high-risk customers", chartClass: "chart chart-sm",
      sub: "How often each feature is among a customer's top three reasons, compared with a typical customer",
      table: () => ({ columns: [{ key: "feature", label: "Factor", format: (v) => FEATURE_LABELS[v] || v },
        { key: "customers", label: "Customers", align: "r", format: (v) => num(v) },
        { key: "avg_impact_pts", label: "Avg impact (pts)", align: "r" }], rows: summary.high_risk_factors }) });
    const bandsPanel = panel({ title: "Risk bands", sub: "Tertiles of churn probability", body: dataTable({ rows: summary.bands, maxHeight: false, columns: [
      { key: "risk_band", label: "Band", format: (v) => h("span", { class: `chip chip-${v.toLowerCase()}` }, v) },
      { key: "customers", label: "Customers", align: "r", format: (v) => num(v) },
      { key: "avg_churn_probability", label: "Avg probability", align: "r", format: (v) => pct(v, 2) },
      { key: "min_churn_probability", label: "Range", align: "r", format: (v, r) => `${pct(v, 2)} to ${pct(r.max_churn_probability, 2)}` },
      { key: "lifetime_revenue", label: "Lifetime revenue", align: "r", format: (v) => brl(v, { compact: true }) },
    ] }) });
    root.append(h("div", { class: "grid grid-2" }, factorPanel.root, bandsPanel.root));

    const bandToggle = h("div", { class: "segmented", role: "group", "aria-label": "Risk band" },
      ...["High", "Medium", "Low"].map((b) => h("button", { type: "button", "aria-pressed": String(b === band),
        onclick: () => { band = b; document.dispatchEvent(new CustomEvent("nexus:rerender")); } }, b)));
    const segSelect = h("select", { class: "select", "aria-label": "Segment", onchange: (e) => { segment = e.target.value; document.dispatchEvent(new CustomEvent("nexus:rerender")); } },
      ...["", "VIP", "Loyal", "Potential", "At Risk", "Lost"].map((s) => h("option", { value: s, selected: s === segment || null }, s || "All segments")));
    root.append(panel({
      title: `${band}-risk customers`, sub: `Highest probability first${params.state ? `, state ${params.state}` : ""}. Up to 200 shown.`,
      actions: [segSelect, bandToggle],
      body: dataTable({ rows: customers, pageSize: 12, maxHeight: false, columns: [
        { key: "customer_short_id", label: "Customer" },
        { key: "segment", label: "Segment" },
        { key: "city", label: "City", format: (v, r) => `${city(v)} (${r.state_code})` },
        { key: "churn_probability", label: "Churn probability", align: "r", format: (v) => pct(v, 2) },
        { key: "monetary", label: "Lifetime revenue", align: "r", format: (v) => brl(v, { decimals: 2 }) },
        { key: "top_factors", label: "Main risk factors", sort: false, format: (fs) => h("ul", { class: "factors" },
          ...(fs.length ? fs.map((f) => h("li", {}, f.description, " ", h("b", {}, `+${f.impact_pts.toFixed(2)} pts`))) : [h("li", {}, "No factor above typical")])) },
      ] }),
    }).root);

    requestAnimationFrame(() => {
      const f = [...summary.high_risk_factors].slice(0, 8).reverse();
      plot(factorPanel.chart, [{ type: "bar", orientation: "h", x: f.map((r) => r.customers), y: f.map((r) => FEATURE_LABELS[r.feature] || r.feature),
        marker: barMarker(t.series[0]), customdata: f.map((r) => r.avg_impact_pts),
        hovertemplate: "%{y}<br>%{x:,} customers, avg +%{customdata:.2f} pts<extra></extra>" }],
        { xaxis: { showgrid: true }, yaxis: { showgrid: false } });
      plot(cmPanel.chart, [{ type: "heatmap", z: [[cm.tp, cm.fn], [cm.fp, cm.tn]], x: ["Predicted churn", "Predicted return"],
        y: ["Actually churned", "Actually returned"], colorscale: [[0, t.seq[0]], [1, t.seq[5]]], showscale: false, xgap: 3, ygap: 3,
        text: [[num(cm.tp), num(cm.fn)], [num(cm.fp), num(cm.tn)]], texttemplate: "%{text}", textfont: { size: 15 },
        hovertemplate: "%{y}, %{x}: %{text}<extra></extra>" }],
        { yaxis: { autorange: "reversed", showgrid: false }, xaxis: { side: "top" } });
    });
  },
};
