// Debrief page: the score, what was found and missed, and how an expert would have done it.

import { api, el, hideOverlay, sessionIdFromPath, showBusy, showError, initChrome } from "./common.js";

const sessionId = sessionIdFromPath();
const root = document.getElementById("debrief");
const signed = (n) => (n > 0 ? `+${n}` : n < 0 ? `−${-n}` : "0");
const turnRef = (turn) => (turn ? ` (command #${turn})` : "");

function scoreHead(d) {
  const s = d.score;
  const outcome = s.passed ? "pass" : "fail";
  return el("div", { class: "score-head" },
    el("div", { class: `grade ${outcome}`, "aria-label": `Grade ${s.grade}` }, s.grade),
    el("div", {},
      el("h1", {}, d.scenario.title),
      el("div", { class: `verdict ${outcome}` },
        s.passed ? "Incident resolved: passed" : s.resolved ? "Not passed: a required step was missed" : "Not passed: the incident wasn't resolved"),
      el("div", { class: "muted" },
        `${s.total} / ${s.maximum} points (${s.percent}%) · ${d.commands_run} commands · ${d.hints_used} hints`),
    ),
  );
}

function breakdown(d) {
  const s = d.score;
  const row = (label, value, cls = "") => el("tr", { class: cls }, el("td", {}, label), el("td", {}, value));
  return el("section", { class: "panel" },
    el("h3", {}, "Score"),
    el("table", { class: "breakdown" },
      row("Checkpoints met", signed(s.earned)),
      row("Key evidence found", signed(s.bonus)),
      row("Hints used", signed(-s.hint_penalty)),
      row("Risky commands", signed(-s.risk_penalty)),
      row("Total", `${s.total} / ${s.maximum}`, "total"),
    ),
  );
}

function checkpoints(d) {
  return el("section", { class: "panel" },
    el("h3", {}, "Checkpoints"),
    el("ul", { class: "checks" }, d.checkpoints.map((c) =>
      el("li", { class: c.met ? "met" : "missed" },
        el("span", { class: "mark", "aria-label": c.met ? "met" : "missed" }, c.met ? "✓" : "✗"),
        el("div", {},
          el("div", {}, c.description, c.required ? el("span", { class: "req" }, "required") : null),
          el("div", { class: "muted small" }, c.feedback + (c.met ? turnRef(c.turn) : "")),
        ),
        el("span", { class: "pts" }, c.met ? `+${c.points}` : `0/${c.points}`),
      ),
    )),
  );
}

function risks(d) {
  if (!d.risks.length) return null;
  return el("section", { class: "panel" },
    el("h3", {}, "Risky commands"),
    d.risks.map((r) => el("div", { class: `risk ${r.level}` },
      el("div", { class: "small" }, el("strong", {}, r.level === "destructive" ? "Destructive" : "High risk"),
        ` · command #${r.turn} · −${r.penalty} points`),
      el("code", {}, r.command),
      el("div", { class: "muted small" }, r.reason),
    )),
  );
}

function list(title, items, cls = "plain", tag = "ul") {
  if (!items.length) return null;
  return el("section", { class: "panel" }, el("h3", {}, title), el(tag, { class: cls }, items.map((i) => el("li", {}, i))));
}

function render(d) {
  document.title = `Incidently: debrief for ${d.scenario.title}`;
  const sections = [
    scoreHead(d),
    el("section", { class: "panel" }, el("h3", {}, "Summary"), el("p", {}, d.summary)),
    el("div", { class: "columns" }, breakdown(d), checkpoints(d)),
    el("section", { class: "panel" },
      el("h3", {}, "What was really wrong"),
      el("p", {}, d.root_cause_explanation),
      el("details", {}, el("summary", { class: "muted small" }, "The scenario's hidden root cause"), el("p", { class: "small" }, d.root_cause)),
    ),
    risks(d),
    el("div", { class: "columns" }, list("What you did well", d.strengths), list("What to do better", d.improvements)),
    list("How an expert would have done it", d.ideal_path, "path", "ol"),
    el("p", {},
      el("button", { class: "primary", onclick: () => playAgain(d.scenario) }, "Play this incident again"),
      " ",
      el("a", { class: "button", href: "/" }, "Back to all incidents"),
    ),
  ];
  root.replaceChildren(...sections.filter(Boolean)); // optional sections are null
}

async function playAgain(scenario) {
  showBusy("Setting up a new incident…", "The root cause may be different this time.");
  try {
    const session = await api("/api/sessions", { method: "POST", body: { scenario_id: scenario.id } });
    location.assign(`/play/${encodeURIComponent(session.id)}`);
  } catch (error) {
    hideOverlay();
    await showError("Couldn't start the incident", error);
  }
}

async function load() {
  try {
    render(await api(`/api/sessions/${encodeURIComponent(sessionId)}/debrief`));
  } catch (error) {
    if (error.status === 409) {
      location.replace(`/play/${encodeURIComponent(sessionId)}`); // not graded yet
      return;
    }
    root.replaceChildren(el("p", { class: "error" }, error.message), el("a", { href: "/" }, "Back to all incidents"));
  }
}

initChrome();
load();
