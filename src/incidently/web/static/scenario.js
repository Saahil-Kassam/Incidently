// Scenario page: what the incident is, the skills it practises, and your attempts at it.

import {
  api, ask, attemptsTable, el, errorState, initChrome, sessionIdFromPath, showError, startIncident,
  usageSummary,
} from "./common.js";

const scenarioId = sessionIdFromPath();
const root = document.getElementById("scenario");

const STATUS = {
  not_started: ["Not started", "not-started"],
  in_progress: ["In progress", "in-progress"],
  passed: ["Passed", "passed"],
  attempted: ["Not passed yet", "attempted"],
};

function actions(detail) {
  const progress = detail.progress;
  const start = (label, cls) => el("button", {
    type: "button",
    class: cls,
    onclick: (event) => startIncident(scenarioId, event.currentTarget),
  }, label);
  const remove = detail.scenario.custom
    ? el("button", { type: "button", class: "danger", onclick: (event) => deleteIncident(detail.scenario, event.currentTarget) }, "Delete incident")
    : null;
  if (!progress.resume_id) return el("div", { class: "actions-row" }, start("Start incident", "primary"), remove);
  return el("div", { class: "actions-row" },
    el("a", { class: "button primary", href: `/play/${encodeURIComponent(progress.resume_id)}` }, "Resume your attempt"),
    start("Start a new attempt", ""),
    remove,
  );
}

async function deleteIncident(scenario, origin) {
  const choice = await ask("Delete this incident?", [
    `“${scenario.title}” will be removed from your hub and its file deleted. This can't be undone.`,
    "Your attempts at it stay in your history, and their debriefs still open.",
  ], [{ label: "Cancel" }, { label: "Delete", class: "danger" }], { origin });
  if (choice !== "Delete") return;
  try {
    await api(`/api/scenarios/${encodeURIComponent(scenario.id)}`, { method: "DELETE" });
    location.assign("/");
  } catch (error) {
    await showError("Couldn't delete the incident", error);
  }
}

// Shown once, on arriving from the hub's "New incident" dialog.
function writtenNote() {
  const stored = sessionStorage.getItem("incidently-written");
  if (!stored) return null;
  sessionStorage.removeItem("incidently-written");
  const written = JSON.parse(stored);
  if (written.id !== scenarioId) return null;
  return el("p", { class: "note", role: "status" }, `New incident written: ${usageSummary(written.usage)}`);
}

function tiles(progress) {
  const [label, cls] = STATUS[progress.status];
  return el("div", { class: "tiles" },
    el("div", { class: "tile" },
      el("div", { class: `value word ${cls}` }, label),
      el("div", { class: "label" }, "Status"),
    ),
    el("div", { class: "tile" },
      el("div", { class: "value" }, progress.best_grade ? `${progress.best_percent}%` : "—"),
      el("div", { class: "label" }, progress.best_grade ? `Best score (${progress.best_grade})` : "Best score"),
    ),
    el("div", { class: "tile" },
      el("div", { class: "value" }, String(progress.attempts)),
      el("div", { class: "label" }, progress.attempts === 1 ? "Attempt" : "Attempts"),
    ),
  );
}

function render(detail) {
  const s = detail.scenario;
  document.title = `Incidently: ${s.title}`;
  document.getElementById("crumb").textContent = s.title;
  root.replaceChildren(
    el("section", { class: "hero" },
      el("div", { class: "tags" },
        el("span", { class: "tag" }, s.category),
        el("span", { class: "tag" }, s.topic),
        el("span", { class: `tag ${s.difficulty}` }, s.difficulty),
      ),
      writtenNote(),
      el("h1", {}, s.title),
      el("p", { class: "lead" }, el("strong", {}, s.summary), " ", detail.description),
      s.skills.length
        ? el("div", {}, el("h3", {}, "Skills you'll practise"), el("ul", { class: "skills" }, s.skills.map((k) => el("li", {}, k))))
        : null,
      actions(detail),
      el("p", { class: "muted small" },
        "Setup takes up to a minute while the incident is generated. Each playthrough can have a different root cause. ",
        "Hints cost 5 points each; risky or destructive commands cost points too."),
    ),
    tiles(detail.progress),
    el("section", { class: "section" },
      el("h2", {}, "Your attempts"),
      detail.attempts.length
        ? attemptsTable(detail.attempts, { withTitle: false })
        : el("div", { class: "state" }, el("p", {}, "You haven't tried this incident yet. Your attempts and scores will show up here.")),
    ),
  );
}

async function load() {
  try {
    render(await api(`/api/scenarios/${encodeURIComponent(scenarioId)}`));
  } catch (error) {
    document.getElementById("crumb").textContent = "Not found";
    root.replaceChildren(errorState(
      error.status === 404 ? "There's no incident here" : "Couldn't load this incident",
      error,
      error.status === 404 ? null : load,
    ), el("p", {}, el("a", { href: "/" }, "Back to all incidents")));
  }
}

initChrome();
load();
