// Scenario hub: list the incidents with your progress, start or resume one, and your past attempts.

import { api, ask, el, hideOverlay, showBusy, showError, showMode } from "./common.js";

const grid = document.getElementById("scenarios");

const BADGES = {
  passed: { label: "Passed", class: "passed" },
  in_progress: { label: "In progress", class: "in-progress" },
  attempted: { label: "Not passed yet", class: "attempted" },
};

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function progressLine(progress) {
  const badge = BADGES[progress.status];
  if (!badge) return null; // not started
  return el("div", { class: "progress" },
    el("span", { class: `badge ${badge.class}` }, badge.label),
    progress.best_grade
      ? el("span", { class: "muted small" }, `Best ${progress.best_percent}% (${progress.best_grade})`)
      : null,
    el("span", { class: "muted small" }, plural(progress.attempts, "attempt")),
  );
}

function card(scenario, progress) {
  return el("button", { class: "card", onclick: () => start(scenario, progress) },
    el("div", { class: "tags" },
      el("span", { class: "tag" }, scenario.category),
      el("span", { class: "tag" }, scenario.topic),
      el("span", { class: `tag ${scenario.difficulty}` }, scenario.difficulty),
    ),
    el("h2", {}, scenario.title),
    el("p", {}, scenario.summary),
    progressLine(progress),
  );
}

async function start(scenario, progress) {
  const resume = progress.resume_id;
  const choice = await ask(scenario.title, [
    scenario.summary,
    resume
      ? el("p", { class: "small" }, "You have an unfinished attempt at this incident. Pick up where you left off, or start a new one.")
      : null,
    el("p", { class: "muted small" },
      "Setup takes up to a minute while the incident is generated. Hints cost 5 points each; " +
      "risky or destructive commands cost points too."),
  ].filter(Boolean), resume
    ? [{ label: "Cancel" }, { label: "Start a new attempt" }, { label: "Resume", class: "primary" }]
    : [{ label: "Cancel" }, { label: "Start incident", class: "primary" }]);

  if (choice === "Resume") {
    location.assign(`/play/${encodeURIComponent(resume)}`);
    return;
  }
  if (choice !== "Start incident" && choice !== "Start a new attempt") return;

  showBusy("Setting up the incident…", "Writing the ticket and preparing the hosts. This usually takes under a minute.");
  try {
    const session = await api("/api/sessions", { method: "POST", body: { scenario_id: scenario.id } });
    location.assign(`/play/${encodeURIComponent(session.id)}`);
  } catch (error) {
    hideOverlay();
    await showError("Couldn't start the incident", error);
  }
}

// --- history ---------------------------------------------------------------------------

function duration(seconds) {
  if (seconds === null) return "";
  const minutes = Math.round(seconds / 60);
  if (minutes < 1) return "under a minute";
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

const when = (iso) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

function result(attempt) {
  const id = encodeURIComponent(attempt.session_id);
  switch (attempt.status) {
    case "in_progress":
      return [el("span", { class: "badge in-progress" }, "In progress"), el("a", { href: `/play/${id}` }, "Resume")];
    case "ungraded":
      return [el("span", { class: "badge attempted" }, "Not graded"), el("a", { href: `/play/${id}` }, "Finish grading")];
    default:
      return [
        el("span", { class: `badge ${attempt.status === "passed" ? "passed" : "attempted"}` },
          `${attempt.grade} · ${attempt.total}/${attempt.maximum}`),
        el("a", { href: `/debrief/${id}` }, "Debrief"),
      ];
  }
}

function historyTable(attempts) {
  return el("table", { class: "history" },
    el("thead", {}, el("tr", {},
      ...["Incident", "Started", "Time taken", "Commands", "Hints", "Result"].map((h) => el("th", { scope: "col" }, h)),
    )),
    el("tbody", {}, attempts.map((a) => el("tr", {},
      el("td", {}, a.title),
      el("td", {}, when(a.started_at)),
      el("td", {}, duration(a.duration_seconds)),
      el("td", { class: "num" }, String(a.commands)),
      el("td", { class: "num" }, String(a.hints)),
      el("td", {}, el("span", { class: "result" }, ...result(a))),
    ))),
  );
}

// --- start -----------------------------------------------------------------------------------

async function load() {
  try {
    const [scenarios, progress, history] = await Promise.all([
      api("/api/scenarios"), api("/api/progress"), api("/api/history"),
    ]);
    const byId = new Map(progress.map((p) => [p.scenario_id, p]));
    grid.replaceChildren(...scenarios.map((s) => card(s, byId.get(s.id))));
    if (history.length) {
      document.getElementById("history").replaceChildren(historyTable(history));
      document.getElementById("history-section").hidden = false;
    }
  } catch (error) {
    grid.replaceChildren(el("p", { class: "error" }, error.message));
  }
}

showMode();
load();
