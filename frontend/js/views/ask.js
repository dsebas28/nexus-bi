// "Ask your data": question -> answer, with every SQL query, its result and a check of every figure.
import * as api from "../api.js";
import { dataTable, h, icons, panel } from "../components.js";
import { escapeHtml, num } from "../format.js";

const history = [];      // answers of this browser session (newest first)
let shown = null;        // the answer currently displayed
let pending = null;      // question being answered
let forceGuided = false; // user chose the no-AI library even though an AI model is available

const icon = {
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>',
};

/** Minimal, safe Markdown: escapes HTML first, then **bold**, `code`, bullet and numbered lists, paragraphs. */
function markdown(text) {
  const inline = (s) => escapeHtml(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let list = null;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    const bullet = line.match(/^[-*•]\s+(.*)/), numbered = line.match(/^\d+[.)]\s+(.*)/);
    if (bullet || numbered) {
      const tag = bullet ? "ul" : "ol";
      if (!list || list.tag !== tag) { if (list) out.push(`</${list.tag}>`); list = { tag }; out.push(`<${tag}>`); }
      out.push(`<li>${inline((bullet || numbered)[1])}</li>`);
      continue;
    }
    if (list) { out.push(`</${list.tag}>`); list = null; }
    if (line) out.push(`<p>${inline(line.replace(/^#+\s*/, ""))}</p>`);
  }
  if (list) out.push(`</${list.tag}>`);
  return out.join("");
}

const STATUS = {
  answered: ["chip-low", "Answered from the data"],
  cannot_answer: ["chip-medium", "The data cannot answer this"],
  error: ["chip-high", "Could not complete"],
};

function answerPanel(r) {
  const [cls, label] = STATUS[r.status] || STATUS.error;
  const executed = r.steps.filter((s) => !s.error).length;
  let verify = null;
  if (r.status === "answered" && executed) {
    verify = r.unverified_numbers.length
      ? h("div", { class: "verify verify-warn", role: "note", html: `${icons.warn}<span><b>Check these figures:</b> ${
        r.unverified_numbers.map(escapeHtml).join(", ")} ${r.unverified_numbers.length === 1 ? "does" : "do"} not appear in any query result below.</span>` })
      : h("div", { class: "verify verify-ok", role: "note", html: `${icon.check}<span>Every figure in this answer was found in the query results below.</span>` });
  }
  const tokens = (r.usage.input_tokens || 0) + (r.usage.cache_read_input_tokens || 0) + (r.usage.cache_creation_input_tokens || 0);
  return panel({
    title: r.question,
    actions: [h("span", { class: `chip ${cls}` }, label)],
    body: h("div", {},
      h("div", { class: "answer", html: markdown(r.answer || "No answer.") }),
      verify,
      h("p", { class: "meta-line" },
        `${r.mode === "guided" ? `Guided mode (no AI) ran ${r.steps.length} prepared ${r.steps.length === 1 ? "query" : "queries"}`
          : `${r.model} wrote ${r.steps.length} ${r.steps.length === 1 ? "query" : "queries"}`}, run as ${r.db_role}. ` +
        `${(r.elapsed_ms / 1000).toFixed(1)} s${tokens ? `, ${num(tokens)} input and ${num(r.usage.output_tokens || 0)} output tokens` : ""}.`)),
  }).root;
}

function stepsPanel(r) {
  if (!r.steps.length) return null;
  const list = h("ol", { class: "steps" });
  for (const s of r.steps) {
    const body = h("div", { style: "min-width:0" },
      h("div", { class: "step-title" }, s.purpose || "Query"),
      h("div", { class: "step-meta" }, s.error ? `Failed after ${s.duration_ms} ms`
        : `${num(s.row_count)} row${s.row_count === 1 ? "" : "s"}${s.truncated ? " (truncated)" : ""} in ${s.duration_ms} ms`),
      h("pre", { class: "sql" }, h("code", {}, s.sql)));
    if (s.error) body.append(h("p", { class: "step-error" }, s.error));
    else if (s.rows.length) {
      const rows = s.rows.map((row) => Object.fromEntries(s.columns.map((c, i) => [c, row[i]])));
      body.append(dataTable({ rows, pageSize: 8, maxHeight: false, caption: s.purpose,
        columns: s.columns.map((c, i) => ({ key: c, label: c, align: typeof s.rows[0][i] === "number" ? "r" : null,
          // Calendar parts (year, month, hour...) are labels, not quantities: no thousands separator.
          format: (v) => (typeof v === "number" ? (/year|month|day|hour|week/i.test(c) ? String(v) : num(v, { decimals: Number.isInteger(v) ? 0 : 2 }))
            : v === null ? "–" : typeof v === "object" ? JSON.stringify(v) : String(v)) })) }));
    }
    list.append(h("li", {}, body));
  }
  return panel({ title: "How this answer was produced",
    sub: `Each SQL query was ${r.mode === "guided" ? "prepared in the guided library" : "written by the model"}, checked by the validator (read-only, allowed views only) and run on PostgreSQL. These are the exact results the answer is based on.`,
    body: list }).root;
}

export default {
  id: "ask",
  filters: [],
  group: "Assistant",
  title: "Ask your data",
  subtitle: "Questions in plain language, answered with SQL you can inspect",

  async render(root) {
    const status = await api.get("/ai/status");
    const guidedOnly = !status.ai_available || forceGuided;
    const examplesList = guidedOnly ? status.guided_questions : status.examples;

    const input = h("textarea", { class: "ask-input", id: "ask-input", maxlength: 500, rows: 3,
      placeholder: guidedOnly ? "e.g. ¿Qué ciudades están creciendo más?  (or pick a question below)"
        : "e.g. ¿Qué ciudades están creciendo más?  or  Which categories lost the most revenue last month?",
      "aria-label": "Your question" });
    input.value = pending || "";
    const button = h("button", { class: "btn btn-primary", type: "submit", disabled: pending ? true : null }, "Ask");
    const token = status.requires_token ? h("input", { class: "select", type: "password", placeholder: "Access token", "aria-label": "Access token",
      style: "width:180px" }) : null;
    if (token) try { token.value = sessionStorage.getItem("nexus-ai-token") || ""; } catch (e) { /* no storage */ }

    const submit = async (question) => {
      question = question.trim();
      if (question.length < 3 || pending) return;
      pending = question;
      document.dispatchEvent(new CustomEvent("nexus:rerender"));
      try {
        if (token) try { sessionStorage.setItem("nexus-ai-token", token.value); } catch (e) { /* no storage */ }
        const result = await api.post("/ai/ask", { question, mode: forceGuided ? "guided" : "auto" },
          token ? { "X-Access-Token": token.value } : {});
        history.unshift(result);
        shown = result;
      } catch (error) {
        shown = { question, mode: status.mode, status: "error", answer: error.message, steps: [], unverified_numbers: [], model: status.model,
          usage: {}, elapsed_ms: 0, db_role: status.db_role };
      } finally {
        pending = null;
        document.dispatchEvent(new CustomEvent("nexus:rerender"));
      }
    };

    const form = h("form", { class: "ask-form", onsubmit: (e) => { e.preventDefault(); submit(input.value); } },
      input,
      h("div", { class: "ask-row" },
        h("span", { class: "ask-hint" }, guidedOnly
          ? "Guided mode answers the questions below (and close rephrasings) with live SQL. No AI, no cost."
          : `Ask anything in Spanish or English. Answers use ${status.model} and only read the analytics views.`),
        h("span", { style: "display:flex; gap:8px" }, token, button)));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(input.value); } });

    const examples = h("div", { class: "chips", "aria-label": "Example questions" },
      ...examplesList.map((q) => h("button", { class: "chip-btn", type: "button", disabled: pending ? true : null,
        onclick: () => { input.value = q; submit(q); } }, q)));

    const modeSwitch = status.ai_available ? h("div", { class: "segmented", role: "group", "aria-label": "Answer mode" },
      ...[[false, status.mode_label], [true, "Guided (no AI)"]].map(([g, label]) => h("button", { type: "button",
        "aria-pressed": String(forceGuided === g), onclick: () => { forceGuided = g; document.dispatchEvent(new CustomEvent("nexus:rerender")); } }, label)))
      : h("span", { class: "chip chip-brand" }, status.mode_label);
    const box = panel({ title: "What would you like to know?", actions: [modeSwitch],
      body: h("div", { class: "ask-form" }, form, examples) }).root;
    root.append(box);

    if (!status.ai_available) {
      root.append(h("div", { class: "callout", role: "note", html: `${icons.warn}<span><b>Running in guided mode (no AI).</b>
        To ask free-form questions for free, install <a href="https://ollama.com/download" target="_blank" rel="noopener">Ollama</a>,
        run <code>ollama pull qwen2.5-coder:7b</code> and reload this page. A Claude API key (<code>ANTHROPIC_API_KEY</code> in
        <code>.env</code>) also works.</span>` }));
    }

    if (pending) {
      const progress = h("div", { class: "progress", role: "status" }, h("span", { class: "spinner" }),
        h("span", {}, guidedOnly ? `Running the SQL for: “${pending}”`
          : `Writing SQL and querying PostgreSQL for: “${pending}”${status.mode === "ollama" ? " (a local model can take a minute or two)" : ""}`));
      root.append(panel({ title: "Working on it", body: progress }).root);
      return;
    }
    if (shown) {
      root.append(answerPanel(shown));
      const steps = stepsPanel(shown);
      if (steps) root.append(steps);
    }
    if (history.length > 1) {
      root.append(panel({ title: "Earlier in this session", body: h("div", { class: "history" },
        ...history.map((r) => h("button", { type: "button", "aria-current": String(r === shown),
          onclick: () => { shown = r; document.dispatchEvent(new CustomEvent("nexus:rerender")); } }, r.question))) }).root);
    }
    requestAnimationFrame(() => { if (!pending && !shown) document.getElementById("ask-input")?.focus(); });
  },
};
