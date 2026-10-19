// Scenario hub: list the incidents and start one.

import { api, ask, el, hideOverlay, showBusy, showError, showMode } from "./common.js";

const grid = document.getElementById("scenarios");

function card(scenario) {
  return el("button", { class: "card", onclick: () => start(scenario) },
    el("div", { class: "tags" },
      el("span", { class: "tag" }, scenario.category),
      el("span", { class: "tag" }, scenario.topic),
      el("span", { class: `tag ${scenario.difficulty}` }, scenario.difficulty),
    ),
    el("h2", {}, scenario.title),
    el("p", {}, scenario.summary),
  );
}

async function start(scenario) {
  const choice = await ask(scenario.title, [
    scenario.summary,
    el("p", { class: "muted small" },
      "Setup takes up to a minute while the incident is generated. Hints cost 5 points each; " +
      "risky or destructive commands cost points too."),
  ], [{ label: "Cancel" }, { label: "Start incident", class: "primary" }]);
  if (choice !== "Start incident") return;

  showBusy("Setting up the incident…", "Writing the ticket and preparing the hosts. This usually takes under a minute.");
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
    const scenarios = await api("/api/scenarios");
    grid.replaceChildren(...scenarios.map(card));
  } catch (error) {
    grid.replaceChildren(el("p", { class: "error" }, error.message));
  }
}

showMode();
load();
