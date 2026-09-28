// Plotly theming from CSS tokens, plus a lightweight SVG sparkline.
// Rules (docs/architecture.md): one y-axis per chart, thin marks, recessive grid,
// fixed categorical order, a legend whenever there are 2+ series, hover on every mark.

export function theme() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name) => cs.getPropertyValue(name).trim();
  return {
    dark: document.documentElement.dataset.theme === "dark",
    font: v("--font"),
    ink: v("--ink"), ink2: v("--ink-2"), ink3: v("--ink-3"),
    line: v("--line"), grid: v("--grid"), surface: v("--surface"),
    brand: v("--brand"), muted: v("--muted-mark"),
    good: v("--good"), bad: v("--bad"),
    series: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => v(`--s${i}`)),
    seq: [1, 2, 3, 4, 5, 6, 7].map((i) => v(`--seq-${i}`)),
    div: [v("--div-neg"), v("--div-mid"), v("--div-pos")],
  };
}

export function sequentialScale(t = theme()) {
  return t.seq.map((c, i) => [i / (t.seq.length - 1), c]);
}

export function divergingScale(t = theme()) {
  return [[0, t.div[0]], [0.5, t.div[1]], [1, t.div[2]]];
}

function merge(base, extra) {
  for (const [k, v] of Object.entries(extra || {})) {
    base[k] = v && typeof v === "object" && !Array.isArray(v) && base[k] && typeof base[k] === "object"
      ? merge({ ...base[k] }, v) : v;
  }
  return base;
}

export function layout(extra = {}) {
  const t = theme();
  const axis = {
    gridcolor: t.grid, linecolor: t.line, zeroline: false, ticks: "",
    tickfont: { color: t.ink3, size: 11 }, title: { font: { color: t.ink2, size: 12 } }, automargin: true,
  };
  return merge({
    font: { family: t.font, size: 12, color: t.ink2 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    margin: { l: 8, r: 12, t: 8, b: 8 },
    xaxis: { ...axis, showgrid: false },
    yaxis: { ...axis, showgrid: true },
    legend: { orientation: "h", x: 0, y: 1.1, yanchor: "bottom", font: { color: t.ink2, size: 12 } },
    hoverlabel: { bgcolor: t.surface, bordercolor: t.line, font: { color: t.ink, family: t.font, size: 12 } },
    hovermode: "closest",
    bargap: 0.3,
    separators: ".,",
  }, extra);
}

const CONFIG = {
  displaylogo: false,
  responsive: true,
  displayModeBar: "hover",
  modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d", "toggleSpikelines", "hoverClosestCartesian",
    "hoverCompareCartesian"],
  toImageButtonOptions: { format: "png", scale: 2 },
};

export function plot(el, data, extraLayout = {}, extraConfig = {}) {
  if (!window.Plotly) {
    el.innerHTML = '<p class="error-box">Charts could not load (Plotly CDN unreachable). The tables still work.</p>';
    return;
  }
  window.Plotly.react(el, data, layout(extraLayout), { ...CONFIG, ...extraConfig });
}

/** Rounded bar ends (4px), as in the chart guidelines. */
export const barMarker = (color) => ({ color, cornerradius: 4, line: { width: 0 } });

/** Minimal inline sparkline: a 2px line with the selected points emphasised. */
export function sparkline(values, highlight = [], { color, muted } = {}) {
  const t = theme();
  const w = 120, h = 28, pad = 3;
  const finite = values.filter((v) => Number.isFinite(v));
  if (finite.length < 2) return "";
  const min = Math.min(...finite), max = Math.max(...finite), span = max - min || 1;
  const x = (i) => pad + (i * (w - 2 * pad)) / (values.length - 1);
  const y = (v) => h - pad - ((v - min) / span) * (h - 2 * pad);
  const pts = values.map((v, i) => (Number.isFinite(v) ? `${x(i).toFixed(1)},${y(v).toFixed(1)}` : null)).filter(Boolean);
  const dots = highlight.map((i) => Number.isFinite(values[i])
    ? `<circle cx="${x(i).toFixed(1)}" cy="${y(values[i]).toFixed(1)}" r="2.6" fill="${color || t.brand}" stroke="${t.surface}" stroke-width="1.2"/>` : "").join("");
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <polyline points="${pts.join(" ")}" fill="none" stroke="${muted || t.muted}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
    ${dots}</svg>`;
}
