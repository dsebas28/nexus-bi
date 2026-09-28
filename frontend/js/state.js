// Global filter state, mirrored in the URL (?start=&end=&state=&category=) so views can be shared.

import { iso, parseDate } from "./format.js";

const listeners = new Set();
let meta = null;
let current = { start: null, end: null, state: "", category: "", preset: "last_month" };

export const PRESETS = [
  { id: "last_month", label: "Last complete month", months: 1 },
  { id: "last_3", label: "Last 3 months", months: 3 },
  { id: "last_6", label: "Last 6 months", months: 6 },
  { id: "last_12", label: "Last 12 months", months: 12 },
  { id: "all", label: "All complete months", months: null },
  { id: "custom", label: "Custom (from timeline)", months: null },
];

const monthEnd = (d) => new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0));

export function presetRange(id) {
  const last = parseDate(meta.reporting_period.last_month);
  const end = iso(monthEnd(last));
  if (id === "all") return { start: meta.reporting_period.first_month, end };
  const preset = PRESETS.find((p) => p.id === id);
  const start = new Date(Date.UTC(last.getUTCFullYear(), last.getUTCMonth() - (preset.months - 1), 1));
  const first = parseDate(meta.reporting_period.first_month);
  return { start: iso(start < first ? first : start), end };
}

function detectPreset(start, end) {
  for (const p of PRESETS.slice(0, 5)) {
    const r = presetRange(p.id);
    if (r.start === start && r.end === end) return p.id;
  }
  return "custom";
}

export function init(metaResponse) {
  meta = metaResponse;
  const q = new URLSearchParams(location.search);
  const start = q.get("start"), end = q.get("end");
  const valid = /^\d{4}-\d{2}-\d{2}$/;
  const range = start && end && valid.test(start) && valid.test(end) && start <= end ? { start, end } : presetRange("last_month");
  current = {
    ...range,
    state: (q.get("state") || "").toUpperCase(),
    category: q.get("category") || "",
    preset: detectPreset(range.start, range.end),
  };
  writeUrl();
}

function writeUrl() {
  const q = new URLSearchParams();
  q.set("start", current.start);
  q.set("end", current.end);
  if (current.state) q.set("state", current.state);
  if (current.category) q.set("category", current.category);
  history.replaceState(null, "", `${location.pathname}?${q}${location.hash}`);
}

export function get() {
  return { ...current };
}

/** Parameters for analytics endpoints. */
export function params() {
  return { start: current.start, end: current.end, state: current.state || null, category: current.category || null };
}

export function set(patch) {
  const next = { ...current, ...patch };
  if (patch.start || patch.end) next.preset = patch.preset || detectPreset(next.start, next.end);
  if (patch.preset && patch.preset !== "custom") Object.assign(next, presetRange(patch.preset));
  current = next;
  writeUrl();
  listeners.forEach((fn) => fn(get()));
}

export function reset() {
  set({ ...presetRange("last_month"), state: "", category: "", preset: "last_month" });
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getMeta() {
  return meta;
}
