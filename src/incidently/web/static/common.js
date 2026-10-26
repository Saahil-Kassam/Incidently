// Shared helpers: API calls, safe DOM building, the page chrome (theme, top bar, shortcuts),
// and the modal overlay.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await send(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw failure(response, data);
  return data;
}

// POST, then call onEvent with each event of the newline-delimited JSON reply as it arrives.
export async function stream(path, body, onEvent) {
  const response = await send(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw failure(response, await response.json().catch(() => ({})));

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    let chunk;
    try {
      chunk = await reader.read();
    } catch {
      throw new ApiError(0, "Lost the connection to Incidently. Is the server still running?");
    }
    if (chunk.done) break;
    buffer += chunk.value;
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line));
  }
}

async function send(path, options) {
  try {
    return await fetch(path, options);
  } catch {
    throw new ApiError(0, "Can't reach Incidently. Is the server still running?");
  }
}

function failure(response, data) {
  const detail = typeof data.detail === "string" ? data.detail : response.statusText;
  return new ApiError(response.status, detail);
}

// Build an element. Children are nodes or strings; strings become text, never HTML.
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function sessionIdFromPath() {
  return decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
}

// An error state with a way forward, for a section that failed to load.
export function errorState(title, error, retry) {
  return el("div", { class: "state error", role: "alert" },
    el("strong", {}, title),
    el("p", {}, error.message || String(error)),
    retry ? el("button", { onclick: retry }, "Try again") : null,
  );
}

// --- formatting ------------------------------------------------------------------------------

export function duration(seconds) {
  if (seconds === null || seconds === undefined) return "";
  const minutes = Math.round(seconds / 60);
  if (minutes < 1) return "under a minute";
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

export const when = (iso) =>
  new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

export const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// --- attempts table (scenario page and progress page) ---------------------------------------

function attemptResult(attempt) {
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

// `withTitle` adds the incident column, for lists that mix scenarios.
export function attemptsTable(attempts, { withTitle = true } = {}) {
  const columns = [withTitle && "Incident", "Started", "Time taken", "Commands", "Hints", "Result"]
    .filter(Boolean);
  return el("div", { class: "table-wrap" }, el("table", { class: "history" },
    el("thead", {}, el("tr", {}, columns.map((h) => el("th", { scope: "col" }, h)))),
    el("tbody", {}, attempts.map((a) => el("tr", {},
      withTitle ? el("td", {}, el("a", { href: `/scenarios/${encodeURIComponent(a.scenario_id)}` }, a.title)) : null,
      el("td", { "data-label": "Started" }, when(a.started_at)),
      el("td", { "data-label": "Time taken" }, duration(a.duration_seconds) || "—"),
      el("td", { class: "num", "data-label": "Commands" }, String(a.commands)),
      el("td", { class: "num", "data-label": "Hints" }, String(a.hints)),
      el("td", {}, el("span", { class: "result" }, ...attemptResult(a))),
    ))),
  ));
}

// Start a new attempt and open it, with a busy state that grows from the button pressed.
export async function startIncident(scenarioId, origin) {
  showBusy("Setting up the incident…", "Writing the ticket and preparing the hosts. This usually takes under a minute.", origin);
  try {
    const session = await api("/api/sessions", { method: "POST", body: { scenario_id: scenarioId } });
    location.assign(`/play/${encodeURIComponent(session.id)}`);
  } catch (error) {
    hideOverlay();
    await showError("Couldn't start the incident", error);
  }
}

// --- page chrome ---------------------------------------------------------------------------

const THEMES = ["system", "light", "dark"];
const THEME_LABELS = { system: "System", light: "Light", dark: "Dark" };
const THEME_ICONS = { system: "◐", light: "☀", dark: "☾" };

// Wire up the top bar: mode, theme toggle, the scroll edge, and keyboard shortcuts.
export function initChrome() {
  showMode();
  initThemeToggle();
  initScrollEdge();
  initShortcuts();
}

async function showMode() {
  const tag = document.querySelector(".mode-tag");
  if (!tag) return;
  try {
    const health = await api("/api/health");
    tag.textContent = health.offline ? "Offline mode: placeholder output" : `Model: ${health.model}`;
    tag.classList.toggle("offline", health.offline);
  } catch {
    tag.textContent = "";
  }
}

function initThemeToggle() {
  const button = document.getElementById("theme-toggle");
  if (!button || !window.incidentlyTheme) return;
  const render = () => {
    const choice = window.incidentlyTheme.choice();
    button.replaceChildren(
      el("span", { "aria-hidden": "true" }, THEME_ICONS[choice]),
      el("span", { class: "label" }, THEME_LABELS[choice]),
    );
    button.setAttribute("aria-label", `Theme: ${THEME_LABELS[choice]}. Change theme`);
  };
  button.addEventListener("click", () => {
    const next = THEMES[(THEMES.indexOf(window.incidentlyTheme.choice()) + 1) % THEMES.length];
    window.incidentlyTheme.set(next);
    render();
  });
  render();
}

// The top bar shows an edge only while content is scrolled beneath it.
function initScrollEdge() {
  const bar = document.querySelector(".topbar");
  if (!bar) return;
  const update = () => bar.classList.toggle("scrolled", window.scrollY > 4);
  window.addEventListener("scroll", update, { passive: true });
  update();
}

// --- keyboard shortcuts -------------------------------------------------------------------------

export const SHORTCUTS = [
  [["/"], "Search incidents"],
  [["Esc"], "Clear the search, or close a dialog"],
  [["g", "i"], "Go to Incidents"],
  [["g", "p"], "Go to Progress"],
  [["?"], "Show these shortcuts"],
];

function typingInto(target) {
  return target.closest?.("input, textarea, select, [contenteditable], .xterm") != null;
}

function initShortcuts() {
  let pendingG = 0; // time the "g" of a two-key shortcut was pressed
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    if (typingInto(event.target) || !overlay().hidden) return;

    if (pendingG && Date.now() - pendingG < 1200) {
      pendingG = 0;
      const go = { i: "/", p: "/progress" }[event.key];
      if (go) {
        event.preventDefault();
        location.assign(go);
      }
      return;
    }
    if (event.key === "g") {
      pendingG = Date.now();
    } else if (event.key === "?") {
      event.preventDefault();
      showShortcuts();
    } else if (event.key === "/") {
      const search = document.querySelector("[data-search]");
      if (search) {
        event.preventDefault();
        search.focus();
        search.select();
      }
    }
  });
}

export function showShortcuts(origin) {
  const keys = (combo) => combo.flatMap((k, i) => [i ? el("span", { class: "then" }, "then") : null, el("kbd", {}, k)]);
  return ask("Keyboard shortcuts", [
    el("dl", { class: "shortcuts" }, SHORTCUTS.flatMap(([combo, what]) => [
      el("dt", {}, keys(combo)),
      el("dd", {}, what),
    ])),
    el("p", { class: "muted small" }, "Shortcuts are off while you type in the terminal or a text box."),
  ], [{ label: "Done", class: "primary" }], { origin });
}

// --- overlay and dialog ------------------------------------------------------------------------
// One overlay per page: a spinner with a message, or a dialog with buttons. Dialogs grow out of
// the control that opened them and shrink back into it, and can be interrupted at any point.

const overlay = () => document.getElementById("overlay");
let closing = null; // timer that hides the overlay once its exit transition has run
let restoreFocus = null;
let dismiss = null; // resolves the open dialog with its safe choice

function open(box, origin) {
  const layer = overlay();
  clearTimeout(closing);
  restoreFocus = origin || document.activeElement;
  layer.replaceChildren(box);
  layer.hidden = false;
  anchor(box, restoreFocus);
  // Start from the closed state, then open on the next frame so the transition runs.
  requestAnimationFrame(() => requestAnimationFrame(() => layer.classList.add("open")));
}

// Point the dialog's transform-origin at the control that opened it.
function anchor(box, origin) {
  if (!origin?.getBoundingClientRect || origin === document.body) return;
  const from = origin.getBoundingClientRect();
  const left = (innerWidth - box.offsetWidth) / 2;
  const top = (innerHeight - box.offsetHeight) / 2;
  const x = from.left + from.width / 2 - left;
  const y = from.top + from.height / 2 - top;
  box.style.transformOrigin = `${Math.round(x)}px ${Math.round(y)}px`;
}

export function showBusy(title, detail, origin) {
  dismiss = null;
  open(el("div", { class: "dialog", role: "status", "aria-live": "polite" },
    el("div", { class: "spinner", "aria-hidden": "true" }),
    el("h2", {}, title),
    detail ? el("p", { class: "muted" }, detail) : null,
  ), origin);
}

export function hideOverlay() {
  const layer = overlay();
  dismiss = null;
  layer.classList.remove("open");
  clearTimeout(closing);
  closing = setTimeout(() => {
    layer.hidden = true;
    layer.replaceChildren();
  }, 220);
  if (restoreFocus?.isConnected) restoreFocus.focus({ preventScroll: true });
  restoreFocus = null;
}

// Resolve with the label of the chosen button. The first button is the safe choice, and is what
// Escape picks. `origin` is the control the dialog appears from (default: the focused element).
export function ask(title, message, buttons, { origin } = {}) {
  return new Promise((resolve) => {
    const choose = (label) => {
      hideOverlay();
      resolve(label);
    };
    const row = el("div", { class: "buttons" },
      buttons.map((b) => el("button", { class: b.class || "", onclick: () => choose(b.label) }, b.label)),
    );
    const box = el("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-label": title },
      el("h2", {}, title),
      ...[].concat(message).filter(Boolean).map((m) => (m instanceof Node ? m : el("p", {}, m))),
      row,
    );
    open(box, origin);
    dismiss = () => choose(buttons[0].label);
    row.querySelector("button:last-child")?.focus();
  });
}

export function showError(title, error, buttons = [{ label: "OK" }]) {
  return ask(title, el("p", { class: "error" }, error.message || String(error)), buttons);
}

// Escape closes a dialog; Tab stays inside it.
document.addEventListener("keydown", (event) => {
  const layer = overlay();
  if (!layer || layer.hidden || !layer.classList.contains("open")) return;
  if (event.key === "Escape" && dismiss) {
    event.preventDefault();
    dismiss();
  } else if (event.key === "Tab") {
    const focusable = [...layer.querySelectorAll("button, a[href], input")];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
});
