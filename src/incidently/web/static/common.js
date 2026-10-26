// Shared helpers: API calls, safe DOM building, and the modal overlay.

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

export async function showMode() {
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

// One overlay per page: a spinner with a message, or a dialog with buttons.
const overlay = () => document.getElementById("overlay");

export function showBusy(title, detail) {
  const box = el("div", { class: "dialog", role: "status", "aria-live": "polite" },
    el("div", { class: "spinner", "aria-hidden": "true" }),
    el("h2", {}, title),
    detail ? el("p", { class: "muted" }, detail) : null,
  );
  overlay().replaceChildren(box);
  overlay().hidden = false;
}

export function hideOverlay() {
  overlay().hidden = true;
  overlay().replaceChildren();
}

// Resolve with the label of the chosen button. The first button is the safe choice.
export function ask(title, message, buttons) {
  return new Promise((resolve) => {
    const choose = (label) => { hideOverlay(); resolve(label); };
    const row = el("div", { class: "buttons" },
      buttons.map((b) => el("button", { class: b.class || "", onclick: () => choose(b.label) }, b.label)),
    );
    const box = el("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-label": title },
      el("h2", {}, title),
      ...[].concat(message).map((m) => (m instanceof Node ? m : el("p", {}, m))),
      row,
    );
    overlay().replaceChildren(box);
    overlay().hidden = false;
    row.querySelector("button:last-child")?.focus();
  });
}

export function showError(title, error, buttons = [{ label: "OK" }]) {
  return ask(title, el("p", { class: "error" }, error.message || String(error)), buttons);
}
