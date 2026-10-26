// Play page: the terminal, the ticket panel, hints, and submitting.

import {
  ApiError, api, ask, el, hideOverlay, sessionIdFromPath, showBusy, showError, initChrome, stream,
} from "./common.js";

const sessionId = sessionIdFromPath();
const CLEAR_WORDS = new Set(["clear", "cls", "clear-host"]);

const style = {
  dim: (s) => `\x1b[2m${s}\x1b[0m`,
  red: (s) => `\x1b[31m${s}\x1b[0m`,
  yellow: (s) => `\x1b[33m${s}\x1b[0m`,
  bold: (s) => `\x1b[1m${s}\x1b[0m`,
};

const term = new Terminal({
  convertEol: true, // the model's output uses \n
  cursorBlink: true,
  fontFamily: 'ui-monospace, "Cascadia Mono", Menlo, Consolas, monospace',
  fontSize: 14,
  scrollback: 5000,
  theme: { background: "#0a0d12", foreground: "#e6edf3", cursor: "#3fb68b" },
});
const fit = new FitAddon.FitAddon();
term.loadAddon(fit);

// --- line editor -----------------------------------------------------------------
// xterm.js is only a display, so editing the input line is done here.

const line = { prompt: "", text: "", cursor: 0 };
const history = [];
let historyIndex = 0;
let busy = true; // until the session has loaded
let ended = false;

function redrawLine() {
  // Return to the start of the line, clear it, and draw prompt + text.
  term.write(`\r\x1b[K${line.prompt}${line.text}`);
  const back = line.text.length - line.cursor;
  if (back > 0) term.write(`\x1b[${back}D`);
}

function showPrompt(prompt) {
  line.prompt = prompt;
  line.text = "";
  line.cursor = 0;
  term.write(prompt);
}

function insert(text) {
  const clean = text.replace(/[\r\n]+/g, " ").replace(/[\x00-\x1f\x7f]/g, "");
  if (!clean) return;
  line.text = line.text.slice(0, line.cursor) + clean + line.text.slice(line.cursor);
  line.cursor += clean.length;
  redrawLine();
}

function recall(step) {
  if (!history.length) return;
  historyIndex = Math.min(history.length, Math.max(0, historyIndex + step));
  line.text = history[historyIndex] ?? "";
  line.cursor = line.text.length;
  redrawLine();
}

term.onData((data) => {
  if (busy || ended) return;
  switch (data) {
    case "\r": // Enter
      submitLine();
      return;
    case "\x7f": // Backspace
    case "\b":
      if (line.cursor > 0) {
        line.text = line.text.slice(0, line.cursor - 1) + line.text.slice(line.cursor);
        line.cursor -= 1;
        redrawLine();
      }
      return;
    case "\x1b[3~": // Delete
      if (line.cursor < line.text.length) {
        line.text = line.text.slice(0, line.cursor) + line.text.slice(line.cursor + 1);
        redrawLine();
      }
      return;
    case "\x1b[A": recall(-1); return; // Up
    case "\x1b[B": recall(1); return; // Down
    case "\x1b[D": // Left
      if (line.cursor > 0) { line.cursor -= 1; term.write(data); }
      return;
    case "\x1b[C": // Right
      if (line.cursor < line.text.length) { line.cursor += 1; term.write(data); }
      return;
    case "\x1b[H": case "\x01": line.cursor = 0; redrawLine(); return; // Home, Ctrl+A
    case "\x1b[F": case "\x05": line.cursor = line.text.length; redrawLine(); return; // End, Ctrl+E
    case "\x03": // Ctrl+C: abandon the line
      term.write("^C\r\n");
      showPrompt(line.prompt);
      return;
    case "\x0c": // Ctrl+L: clear the screen, keep the line
      term.clear();
      redrawLine();
      return;
    case "\x15": // Ctrl+U: delete to start of line
      line.text = line.text.slice(line.cursor);
      line.cursor = 0;
      redrawLine();
      return;
    default:
      if (data.startsWith("\x1b")) return; // other keys we don't handle
      insert(data); // typing or paste
  }
});

// --- commands ----------------------------------------------------------------------

function setStatus(text) {
  const status = document.getElementById("status");
  status.textContent = text;
  status.hidden = !text;
}

const withNewline = (s) => (s.endsWith("\n") ? s : `${s}\n`);

// End a partly written line, so what comes next starts on a line of its own.
function breakLine(shown) {
  if (shown && !shown.endsWith("\n")) term.write("\n");
}

// Write a command's output. `shown` is the stdout already streamed to the terminal.
function writeOutput({ stdout, stderr }, shown = "") {
  if (!stdout.startsWith(shown)) {
    breakLine(shown);
    term.write(style.yellow("[incidently] The output changed as it was written. In full:\n"));
    shown = "";
  }
  if (stdout) term.write(withNewline(stdout).slice(shown.length));
  if (stderr) term.write(style.red(withNewline(stderr)));
}

let streamed = ""; // stdout of the running command written so far

// Run a command, writing its stdout as the model produces it, and resolve with the final turn.
async function runCommand(command) {
  let turn = null;
  await stream(`/api/sessions/${encodeURIComponent(sessionId)}/commands/stream`, { line: command }, (event) => {
    if (event.type === "output") {
      term.write(event.text);
      streamed += event.text;
    } else if (event.type === "restart") {
      breakLine(streamed);
      term.write(style.yellow("[incidently] That output was cut off; writing it again.\n"));
      streamed = "";
    } else if (event.type === "turn") {
      turn = event.turn;
    } else if (event.type === "error") {
      throw new ApiError(500, event.detail);
    }
  });
  if (!turn) throw new ApiError(0, "The command stopped without finishing. Reload the page to see where things stand.");
  return turn;
}

async function submitLine() {
  const command = line.text;
  term.write("\r\n");
  if (command.trim()) {
    history.push(command);
  }
  historyIndex = history.length;

  busy = true;
  setStatus("Running…");
  streamed = "";
  try {
    const turn = await runCommand(command);
    if (turn.clear_screen) term.clear();
    if (!turn.ok) {
      // The streamed output doesn't count; the notice below says why.
      breakLine(streamed);
      streamed = "";
    }
    writeOutput(turn, streamed);
    if (turn.notice) term.write(style.yellow(`[incidently] ${turn.notice}\n`));
    showPrompt(turn.prompt);
    if (turn.end_requested) {
      busy = false;
      setStatus("");
      await confirmFinish();
      return;
    }
  } catch (error) {
    breakLine(streamed);
    term.write(style.yellow(`[incidently] ${error.message}\n`));
    showPrompt(line.prompt);
  }
  busy = false;
  setStatus("");
}

// --- side panel ---------------------------------------------------------------------

function renderPanel(session) {
  document.title = `Incidently: ${session.scenario.title}`;
  document.getElementById("title").textContent = session.scenario.title;
  const t = session.ticket;
  document.getElementById("ticket").replaceChildren(
    el("div", { class: "ticket-head" },
      el("span", { class: "priority" }, t.priority),
      el("code", {}, t.id),
      el("span", { class: "muted small" }, `from ${t.reported_by}`),
    ),
    el("p", {}, el("strong", {}, t.summary)),
    el("p", { class: "ticket-body small" }, t.description),
  );
  document.getElementById("objective").textContent = session.objective;
  document.getElementById("hosts").replaceChildren(...session.hosts.map((h) =>
    el("li", {},
      el("div", {}, el("span", { class: "name" }, h.name), " ", el("span", { class: "muted small" }, h.ip)),
      el("div", { class: "small" }, h.os),
      el("div", { class: "muted small" }, h.role),
    ),
  ));
  renderHints(session.hints, session.hints_total);
}

function renderHints(hints, total) {
  document.getElementById("hints").replaceChildren(...hints.map((h) => el("li", {}, h)));
  const button = document.getElementById("hint-button");
  const left = total - hints.length;
  button.disabled = left <= 0 || ended;
  button.textContent = left > 0 ? `Get a hint (−5 points, ${left} left)` : "No hints left";
}

const hints = [];
let hintsTotal = 0;

async function takeHint() {
  if (busy) return;
  const choice = await ask("Take a hint?", "Each hint costs 5 points at grading.", [
    { label: "Cancel" },
    { label: "Show hint", class: "primary" },
  ]);
  if (choice !== "Show hint") return;
  const button = document.getElementById("hint-button");
  button.disabled = true;
  button.textContent = "Thinking…";
  try {
    const hint = await api(`/api/sessions/${encodeURIComponent(sessionId)}/hint`, { method: "POST" });
    hints.push(hint.text);
  } catch (error) {
    await showError("Couldn't get a hint", error);
  }
  renderHints(hints, hintsTotal);
  term.focus();
}

async function confirmFinish() {
  if (busy) return; // a command is still running
  const choice = await ask("End the incident?", [
    "Your work will be graded now and you won't be able to run more commands.",
  ], [{ label: "Keep working" }, { label: "End and grade", class: "primary" }]);
  if (choice === "End and grade") await finish();
  else term.focus();
}

async function finish() {
  ended = true;
  busy = true;
  renderHints(hints, hintsTotal);
  document.getElementById("submit-button").disabled = true;
  showBusy("Grading your work…", "Reviewing every command you ran. This usually takes under a minute.");
  try {
    await api(`/api/sessions/${encodeURIComponent(sessionId)}/finish`, { method: "POST" });
    location.assign(`/debrief/${encodeURIComponent(sessionId)}`);
  } catch (error) {
    hideOverlay();
    const choice = await showError("Grading failed", error, [{ label: "Back to hub" }, { label: "Try again", class: "primary" }]);
    if (choice === "Try again") return finish();
    location.assign("/");
  }
}

// --- start -------------------------------------------------------------------------------

function replay(session) {
  term.write(style.bold(`${session.ticket.id}: ${session.ticket.summary}\n`));
  term.write(style.dim("Investigate with real commands. Arrow keys recall history; Ctrl+C clears the line.\n\n"));
  for (const entry of session.transcript) {
    if (CLEAR_WORDS.has(entry.command.trim().toLowerCase())) {
      term.clear();
      continue;
    }
    term.write(`${entry.prompt}${entry.command}\n`);
    writeOutput(entry);
    if (entry.command.trim()) history.push(entry.command);
  }
  historyIndex = history.length;
}

async function load() {
  term.open(document.getElementById("terminal"));
  fit.fit();
  new ResizeObserver(() => fit.fit()).observe(document.getElementById("terminal"));

  let session;
  try {
    session = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
  } catch (error) {
    await showError("Couldn't open this incident", error, [{ label: "Back to hub" }]);
    location.assign("/");
    return;
  }
  if (session.graded) {
    location.replace(`/debrief/${encodeURIComponent(sessionId)}`);
    return;
  }
  hints.push(...session.hints);
  hintsTotal = session.hints_total;
  renderPanel(session);
  replay(session);
  showPrompt(session.prompt);

  if (session.ended) {
    // Submitted earlier, but grading didn't finish.
    await finish();
    return;
  }
  busy = false;
  term.focus();
}

document.getElementById("hint-button").addEventListener("click", takeHint);
document.getElementById("submit-button").addEventListener("click", confirmFinish);
initChrome();
load();
