// Entry page: live facts and the revenue ribbon, loaded from the API.
import * as api from "./api.js";
import { brl, monthLabel, num, parseDate } from "./format.js";

const NS = "http://www.w3.org/2000/svg";

function drawRibbon(months) {
  const svg = document.getElementById("l-ribbon");
  const w = svg.clientWidth || 1000, h = 120, labelH = 16;
  const max = Math.max(...months.map((m) => m.revenue));
  const peak = months.find((m) => m.revenue === max);
  const slot = w / months.length, barW = slot * 0.72;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  months.forEach((m, i) => {
    const bh = (m.revenue / max) * (h - labelH - 4);
    const r = document.createElementNS(NS, "rect");
    Object.entries({ x: i * slot + (slot - barW) / 2, y: h - labelH - bh, width: barW, height: bh, rx: 3 })
      .forEach(([k, v]) => r.setAttribute(k, v));
    if (m === peak) r.setAttribute("class", "peak");
    const title = document.createElementNS(NS, "title");
    title.textContent = `${monthLabel(m.month)}: ${brl(m.revenue)}`;
    r.append(title);
    svg.append(r);
    if (parseDate(m.month).getUTCMonth() === 0 || i === 0) {
      const t = document.createElementNS(NS, "text");
      t.setAttribute("x", i * slot + 2); t.setAttribute("y", h - 3);
      t.textContent = parseDate(m.month).getUTCFullYear();
      svg.append(t);
    }
  });
  document.getElementById("l-ribbon-caption").textContent =
    `Monthly revenue, ${monthLabel(months[0].month)} to ${monthLabel(months.at(-1).month)}. Peak: ${monthLabel(peak.month)} (${brl(peak.revenue)})${parseDate(peak.month).getUTCMonth() === 10 ? ", the month of Black Friday" : ""}.`;
}

async function main() {
  try {
    const meta = await api.get("/meta");
    const p = meta.reporting_period;
    const lastDay = new Date(Date.UTC(parseDate(p.last_month).getUTCFullYear(), parseDate(p.last_month).getUTCMonth() + 1, 0));
    const [monthly, kpis] = await Promise.all([
      api.get("/sales/monthly"),
      api.get("/kpis", { start: p.first_month, end: lastDay.toISOString().slice(0, 10) }),
    ]);
    drawRibbon(monthly.filter((m) => m.is_complete));
    const k = Object.fromEntries(kpis.kpis.map((x) => [x.metric, x.current_value]));
    const facts = [
      ["Revenue in complete months", brl(k.revenue, { compact: true })],
      ["Orders", num(k.orders)],
      ["Customers", num(k.customers)],
      ["Reliable data", `${monthLabel(p.data_start).slice(0, 3)} ’${String(parseDate(p.data_start).getUTCFullYear()).slice(2)} to ${monthLabel(p.data_end).slice(0, 3)} ’${String(parseDate(p.data_end).getUTCFullYear()).slice(2)}`],
    ];
    document.getElementById("l-facts").innerHTML = facts.map(([dt, dd]) => `<div><dt>${dt}</dt><dd>${dd}</dd></div>`).join("");
  } catch (error) {
    document.getElementById("l-ribbon-caption").textContent =
      "The API is not reachable. Start it with: python -m uvicorn backend.main:app --reload";
  }
}

main();
