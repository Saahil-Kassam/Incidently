// Progress dashboard: totals, the skills you've proven, and every attempt.

import { api, attemptsTable, duration, el, errorState, initChrome, plural } from "./common.js";

const root = document.getElementById("progress");

function tile(value, label, of = null) {
  return el("div", { class: "tile" },
    el("div", { class: "value" }, value, of ? el("span", { class: "of" }, ` / ${of}`) : null),
    el("div", { class: "label" }, label),
  );
}

function tiles(stats) {
  const percent = (n) => (n === null ? "—" : `${n}%`);
  return el("div", { class: "tiles" },
    tile(String(stats.incidents_passed), "Incidents passed", stats.incidents),
    tile(percent(stats.pass_rate), stats.graded ? `Pass rate (${plural(stats.graded, "graded attempt")})` : "Pass rate"),
    tile(percent(stats.average_percent), "Average score"),
    tile(String(stats.attempts), stats.attempts === 1 ? "Attempt" : "Attempts"),
    tile(stats.seconds_played ? duration(stats.seconds_played) : "—", "Time spent on submitted attempts"),
  );
}

function skills(list) {
  if (!list.length) return null;
  const items = list.map((s) => {
    const meter = el("span", { style: "--fill: 0" });
    // Grow each bar from empty on the next frame, so the fill reads as progress being made.
    requestAnimationFrame(() => requestAnimationFrame(() => meter.style.setProperty("--fill", String(s.passed / s.incidents))));
    return el("li", {},
      el("span", {}, s.skill),
      el("div", {
        class: "meter",
        role: "meter",
        "aria-label": s.skill,
        "aria-valuemin": "0",
        "aria-valuemax": String(s.incidents),
        "aria-valuenow": String(s.passed),
      }, meter),
      el("span", { class: "count" }, `${s.passed} of ${plural(s.incidents, "incident")} passed`),
    );
  });
  return el("section", { class: "section" },
    el("h2", {}, "Skills"),
    el("p", { class: "muted small" }, "A skill counts once you've passed an incident that practises it."),
    el("div", { class: "panel" }, el("ul", { class: "skill-list" }, items)),
  );
}

function history(attempts) {
  return el("section", { class: "section" },
    el("h2", {}, "All attempts"),
    attempts.length
      ? attemptsTable(attempts)
      : el("div", { class: "state" },
        el("p", {}, "No attempts yet. Pick an incident to get started; every attempt is saved here."),
        el("a", { class: "button primary", href: "/" }, "Browse incidents"),
      ),
  );
}

async function load() {
  root.replaceChildren(el("p", { class: "muted" }, "Loading your progress…"));
  try {
    const [stats, attempts] = await Promise.all([api("/api/stats"), api("/api/history")]);
    root.replaceChildren(...[tiles(stats), skills(stats.skills), history(attempts)].filter(Boolean));
  } catch (error) {
    root.replaceChildren(errorState("Couldn't load your progress", error, load));
  }
}

initChrome();
load();
