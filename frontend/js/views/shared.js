// Helpers shared by several views.
import * as api from "../api.js";
import { brl, iso, monthLabel, num, parseDate, pct, rangeLabel } from "../format.js";

export function formatKpi(k) {
  if (k.current_value === null) return "–";
  if (k.unit === "currency") return brl(k.current_value, { compact: k.metric !== "avg_order_value", decimals: k.metric === "avg_order_value" ? 2 : 0 });
  if (k.unit === "ratio") return pct(k.current_value);
  return num(k.current_value);
}

/** Revenue of the same period one year earlier, if that period lies inside the data window. */
export async function yearOverYear(filters, params, meta) {
  const shift = (d) => { const x = parseDate(d); x.setUTCFullYear(x.getUTCFullYear() - 1); return iso(x); };
  const start = shift(filters.start);
  let end = shift(filters.end);
  if (filters.end.endsWith("-02-29")) end = end.replace("-03-01", "-02-28");
  if (start < meta.reporting_period.first_month) return null;
  const [cur, prev] = await Promise.all([
    api.get("/kpis", params),
    api.get("/kpis", { ...params, start, end }),
  ]);
  const rev = (r) => r.kpis.find((k) => k.metric === "revenue").current_value;
  return { current: rev(cur), previous: rev(prev), label: rangeLabel(start, end) };
}

export function compareLabel(filters) {
  return rangeLabel(filters.previous_start, filters.previous_end);
}

/** The last `n` complete months of a monthly series, with indexes of the selected period. */
export function recentMonths(monthly, filters, n = 12) {
  const complete = monthly.filter((m) => m.is_complete);
  const endIdx = Math.max(complete.findIndex((m) => m.month > filters.end) - 1, -1);
  const last = endIdx >= 0 ? endIdx : complete.length - 1;
  const series = complete.slice(Math.max(0, last - n + 1), last + 1);
  const selectedIdx = series.map((m, i) => (m.month >= filters.start.slice(0, 8) + "01" && m.month <= filters.end ? i : -1))
    .filter((i) => i >= 0);
  return { series, selectedIdx };
}

export const monthTick = (m) => monthLabel(m);
