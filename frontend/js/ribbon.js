// Timeline ribbon: one bar per complete month (revenue under the current state/category
// filters). It shows the selected period and the period it is compared with, and it is
// the period control itself: click a month, drag across months, or use the keyboard.

import * as state from "./state.js";
import { brl, iso, monthLabel, parseDate, rangeLabel } from "./format.js";

const NS = "http://www.w3.org/2000/svg";
let months = [];
let dragFrom = null;

const svg = () => document.getElementById("ribbon-svg");
const monthEnd = (m) => { const d = parseDate(m); return iso(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0))); };

function node(tag, attrs) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function selectRange(i, j) {
  const [a, b] = i <= j ? [i, j] : [j, i];
  state.set({ start: months[a].month, end: monthEnd(months[b].month), preset: "custom" });
}

export function setData(rows) {
  months = rows.filter((r) => r.is_complete);
}

export function render(filters, meta) {
  const el = svg();
  if (!el) return;
  const width = el.clientWidth || 900, height = 76, top = 6, labelH = 18;
  const n = months.length;
  el.setAttribute("viewBox", `0 0 ${width} ${height}`);
  el.replaceChildren();
  if (!n) return;

  const prev = { start: filters.previous_start, end: filters.previous_end };
  const max = Math.max(...months.map((m) => m.revenue)) || 1;
  const slot = width / n, barW = Math.max(4, slot - Math.min(6, slot * 0.28));
  const plotH = height - top - labelH;

  months.forEach((m, i) => {
    const x = i * slot + (slot - barW) / 2;
    const bh = Math.max(2, (m.revenue / max) * plotH);
    const inCurrent = m.month >= filters.start.slice(0, 8) + "01" && m.month <= filters.end;
    const inPrev = prev.start && m.month >= prev.start.slice(0, 8) + "01" && m.month <= prev.end;
    const cls = inCurrent ? "bar-current" : inPrev ? "bar-previous" : "bar-bg";
    el.append(node("rect", { class: `bar ${cls}`, x, y: top + plotH - bh, width: barW, height: bh, rx: Math.min(3, barW / 3) }));

    const d = parseDate(m.month);
    if (d.getUTCMonth() === 0 || i === 0) {
      const year = node("text", { class: "year", x: i * slot + 2, y: height - 3 });
      year.textContent = d.getUTCFullYear();
      el.append(year);
    } else if (d.getUTCMonth() % 3 === 0 && slot > 16) {
      const tick = node("text", { class: "tick", x: i * slot + slot / 2, y: height - 3, "text-anchor": "middle" });
      tick.textContent = monthLabel(m.month).slice(0, 3);
      el.append(tick);
    }

    // Full-height hit area: bigger than the mark, focusable, with a native tooltip.
    const hit = node("rect", { class: "hit", x: i * slot, y: 0, width: slot, height: height - labelH + 2, tabindex: 0,
      role: "button", "aria-label": `${monthLabel(m.month)}: revenue ${brl(m.revenue)}${inCurrent ? " (selected)" : ""}` });
    const title = node("title", {});
    title.textContent = `${monthLabel(m.month)}: ${brl(m.revenue)}`;
    hit.append(title);
    // Touch input captures the pointer implicitly; release it so pointerenter fires on the next months.
    hit.addEventListener("pointerdown", (e) => {
      dragFrom = i;
      if (e.target.hasPointerCapture?.(e.pointerId)) e.target.releasePointerCapture(e.pointerId);
    });
    hit.addEventListener("pointerenter", () => { if (dragFrom !== null) preview(dragFrom, i); });
    hit.addEventListener("pointerup", () => { if (dragFrom !== null) { selectRange(dragFrom, i); dragFrom = null; } });
    hit.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectRange(i, i); }
      if (e.key === "ArrowRight") el.querySelectorAll(".hit")[Math.min(n - 1, i + 1)].focus();
      if (e.key === "ArrowLeft") el.querySelectorAll(".hit")[Math.max(0, i - 1)].focus();
    });
    el.append(hit);
  });

  const label = document.getElementById("ribbon-period");
  label.innerHTML = `Showing <strong>${rangeLabel(filters.start, filters.end)}</strong>, compared with ${
    rangeLabel(prev.start, prev.end)}`;
}

function preview(i, j) {
  const [a, b] = i <= j ? [i, j] : [j, i];
  svg().querySelectorAll(".bar").forEach((bar, k) => {
    bar.classList.toggle("bar-current", k >= a && k <= b);
    bar.classList.toggle("bar-bg", !(k >= a && k <= b));
    bar.classList.remove("bar-previous");
  });
}

window.addEventListener("pointerup", () => { dragFrom = null; });
