// Number, currency and date formatting. Currency is Brazilian reais (R$).

const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const minus = (v) => (v < 0 ? "−" : "");

export function brl(v, { compact = false, decimals = 0 } = {}) {
  if (!isNum(v)) return "–";
  const a = Math.abs(v);
  if (compact && a >= 1e6) return `${minus(v)}R$ ${nf2.format(a / 1e6)}M`;
  if (compact && a >= 1e3) return `${minus(v)}R$ ${nf1.format(a / 1e3)}K`;
  return `${minus(v)}R$ ${(decimals === 2 ? nf2 : nf0).format(a)}`;
}

export function num(v, { compact = false, decimals = 0 } = {}) {
  if (!isNum(v)) return "–";
  const a = Math.abs(v);
  if (compact && a >= 1e6) return `${minus(v)}${nf2.format(a / 1e6)}M`;
  if (compact && a >= 1e4) return `${minus(v)}${nf1.format(a / 1e3)}K`;
  const f = decimals === 2 ? nf2 : decimals === 1 ? nf1 : nf0;
  return `${minus(v)}${f.format(a)}`;
}

/** Ratio (0.123) to percent text. */
export function pct(v, decimals = 1) {
  if (!isNum(v)) return "–";
  return `${minus(v)}${Math.abs(v * 100).toFixed(decimals)}%`;
}

/** Signed change: ratio (0.05 -> +5.0%). */
export function signedPct(v, decimals = 1) {
  if (!isNum(v)) return "–";
  return `${v > 0 ? "+" : minus(v)}${Math.abs(v * 100).toFixed(decimals)}%`;
}

/** Percentage points from a ratio difference (0.004 -> +0.4 pts). */
export function pts(v, decimals = 1) {
  if (!isNum(v)) return "–";
  return `${v > 0 ? "+" : minus(v)}${Math.abs(v * 100).toFixed(decimals)} pts`;
}

export function parseDate(iso) {
  const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

export function iso(date) {
  return date.toISOString().slice(0, 10);
}

export function monthLabel(isoDate) {
  const d = parseDate(isoDate);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

export function dayLabel(isoDate) {
  const d = parseDate(isoDate);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** Human label for a date range: "Jul 2018", "Apr – Jun 2018", or "3 – 17 Jul 2018". */
export function rangeLabel(start, end) {
  const s = parseDate(start), e = parseDate(end);
  const lastDay = new Date(Date.UTC(e.getUTCFullYear(), e.getUTCMonth() + 1, 0)).getUTCDate();
  const wholeMonths = s.getUTCDate() === 1 && e.getUTCDate() === lastDay;
  if (wholeMonths) {
    if (s.getUTCFullYear() === e.getUTCFullYear() && s.getUTCMonth() === e.getUTCMonth()) return monthLabel(start);
    const sameYear = s.getUTCFullYear() === e.getUTCFullYear();
    return `${MONTHS[s.getUTCMonth()]}${sameYear ? "" : ` ${s.getUTCFullYear()}`} – ${monthLabel(end)}`;
  }
  return `${dayLabel(start)} – ${dayLabel(end)}`;
}

/** Olist category names are snake_case Portuguese-English: make them readable. */
export function category(name) {
  if (!name) return "–";
  const s = String(name).replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Olist stores cities in lowercase ASCII: "sao jose dos campos" -> "Sao Jose dos Campos". */
export function city(name) {
  const small = new Set(["de", "da", "do", "das", "dos", "e"]);
  return String(name ?? "").split(" ").map((w, i) => (i > 0 && small.has(w) ? w : w.charAt(0).toUpperCase() + w.slice(1))).join(" ");
}

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
