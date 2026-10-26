// Scenario hub: every incident with your progress, narrowed by search and filters.
// Filters live in the URL, so a filtered view survives a reload and the back button.

import { api, el, errorState, initChrome, plural } from "./common.js";

const grid = document.getElementById("scenarios");
const search = document.getElementById("search");
const count = document.getElementById("result-count");

const BADGES = {
  passed: { label: "Passed", class: "passed" },
  in_progress: { label: "In progress", class: "in-progress" },
  attempted: { label: "Not passed yet", class: "attempted" },
};
const STATUS_LABELS = {
  not_started: "Not started",
  in_progress: "In progress",
  passed: "Passed",
  attempted: "Not passed yet",
};
const DIFFICULTY_ORDER = ["beginner", "intermediate", "advanced"];

let scenarios = [];
let progressById = new Map();
const state = { q: "", category: "", difficulty: "", status: "" };

// --- cards ---------------------------------------------------------------------------------------

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

function card(scenario) {
  return el("a", { class: "card", href: `/scenarios/${encodeURIComponent(scenario.id)}` },
    el("div", { class: "tags" },
      el("span", { class: "tag" }, scenario.category),
      el("span", { class: "tag" }, scenario.topic),
      el("span", { class: `tag ${scenario.difficulty}` }, scenario.difficulty),
    ),
    el("h2", {}, scenario.title),
    el("p", {}, scenario.summary),
    progressLine(progressById.get(scenario.id)),
  );
}

// --- filtering ---------------------------------------------------------------------------------

function haystack(s) {
  return [s.title, s.summary, s.category, s.topic, s.difficulty, ...s.skills].join(" ").toLowerCase();
}

function matches(s) {
  const words = state.q.toLowerCase().split(/\s+/).filter(Boolean);
  const text = haystack(s);
  return words.every((w) => text.includes(w))
    && (!state.category || s.category === state.category)
    && (!state.difficulty || s.difficulty === state.difficulty)
    && (!state.status || progressById.get(s.id).status === state.status);
}

const filtered = () => Object.values(state).some(Boolean);

function clearFilters() {
  Object.assign(state, { q: "", category: "", difficulty: "", status: "" });
  search.value = "";
  update();
  search.focus();
}

function render() {
  const shown = scenarios.filter(matches);
  if (shown.length) {
    grid.replaceChildren(...shown.map(card));
  } else {
    grid.replaceChildren(el("div", { class: "state" },
      el("p", {}, state.q ? `No incidents match “${state.q}” with these filters.` : "No incidents match these filters."),
      el("button", { onclick: clearFilters }, "Clear search and filters"),
    ));
  }
  count.textContent = filtered() ? `Showing ${shown.length} of ${plural(scenarios.length, "incident")}` : "";
}

function chipGroup(name, label, options) {
  // A group with one choice can't narrow anything, so it isn't shown.
  if (options.length < 2) return null;
  const chip = (value, text) => el("button", {
    type: "button",
    class: "chip",
    "aria-pressed": String(state[name] === value),
    onclick: () => { state[name] = value; update(); },
  }, text);
  return el("div", { class: "chips", role: "group", "aria-label": label },
    el("span", { class: "chips-label", "aria-hidden": "true" }, label),
    chip("", "All"),
    options.map(([value, text]) => chip(value, text)),
  );
}

function renderFilters() {
  const unique = (values) => [...new Set(values)];
  const categories = unique(scenarios.map((s) => s.category)).sort().map((c) => [c, c]);
  const difficulties = DIFFICULTY_ORDER
    .filter((d) => scenarios.some((s) => s.difficulty === d))
    .map((d) => [d, d[0].toUpperCase() + d.slice(1)]);
  // Only statuses that apply to something, plus the active one so it can always be seen.
  const statuses = Object.entries(STATUS_LABELS).filter(([status]) =>
    state.status === status || scenarios.some((s) => progressById.get(s.id).status === status));
  document.getElementById("filters").replaceChildren(...[
    chipGroup("category", "Category", categories),
    chipGroup("difficulty", "Difficulty", difficulties),
    chipGroup("status", "Status", statuses),
  ].filter(Boolean));
}

function saveToUrl() {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(state)) if (value) params.set(key, value);
  const query = params.toString();
  history.replaceState(null, "", query ? `/?${query}` : "/");
}

function readFromUrl() {
  const params = new URLSearchParams(location.search);
  for (const key of Object.keys(state)) state[key] = params.get(key) || "";
  search.value = state.q;
}

function update() {
  saveToUrl();
  renderFilters();
  render();
}

// Filter as you type: no debounce, the list is small and local.
search.addEventListener("input", () => {
  state.q = search.value.trim();
  saveToUrl();
  render();
});
search.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (search.value) {
    search.value = "";
    state.q = "";
    saveToUrl();
    render();
  } else {
    search.blur();
  }
});

// --- start -------------------------------------------------------------------------------------

async function load() {
  grid.replaceChildren(el("p", { class: "muted" }, "Loading incidents…"));
  try {
    const [list, progress] = await Promise.all([api("/api/scenarios"), api("/api/progress")]);
    scenarios = list;
    progressById = new Map(progress.map((p) => [p.scenario_id, p]));
    readFromUrl();
    update();
  } catch (error) {
    count.textContent = "";
    grid.replaceChildren(errorState("Couldn't load the incidents", error, load));
  }
}

initChrome();
load();
