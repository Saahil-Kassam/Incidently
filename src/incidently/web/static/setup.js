// API key page: the first thing a new player sees, and where the key is changed later.
// The server only ever reports the key's last four characters; the key itself goes one way.

import { api, ask, el, errorState, initChrome } from "./common.js";

const root = document.getElementById("setup");
const KEYS_URL = "https://console.anthropic.com/settings/keys";

const SOURCES = {
  saved: "saved on this computer",
  entered: "entered for this session only",
  environment: "from ANTHROPIC_API_KEY in your environment or .env file",
};

// Where to go once there's a key: the page that sent us here, if it's one of ours.
function next() {
  const target = new URLSearchParams(location.search).get("next") || "/";
  return target.startsWith("/") && !target.startsWith("//") && !target.startsWith("/setup") ? target : "/";
}

function intro(status) {
  if (status.configured) {
    return [
      el("h1", {}, "Your API key"),
      el("p", { class: "lead" },
        "Incidently uses Claude to play the hosts, write tickets, and grade your work, billed to " +
        "your own Anthropic account. You can replace the key here, or forget it."),
    ];
  }
  return [
    el("h1", {}, "Connect your Anthropic API key"),
    el("p", { class: "lead" },
      "Incidently uses Claude to play the hosts, write tickets, and grade your work, so every " +
      "incident is billed to your own Anthropic account. Add an API key to get started."),
  ];
}

function current(status) {
  if (!status.configured) return null;
  const forget = status.source === "saved" || status.source === "entered"
    ? el("button", { type: "button", class: "danger", onclick: (event) => forgetKey(status, event.currentTarget) }, "Forget this key")
    : null;
  return el("section", { class: "panel key-status" },
    el("p", {}, "Using the key ending ", el("code", {}, status.hint), `, ${SOURCES[status.source]}.`),
    el("div", { class: "actions-row" },
      el("a", { class: "button primary", href: next() }, "Continue to incidents"),
      forget,
    ),
  );
}

function form(status) {
  const input = el("input", {
    id: "api-key",
    type: "password",
    name: "api_key",
    required: true,
    maxlength: "400",
    autocomplete: "off",
    spellcheck: "false",
    autocapitalize: "off",
    placeholder: "sk-ant-…",
    "aria-describedby": "key-help key-error",
  });
  const reveal = el("button", {
    type: "button",
    class: "reveal",
    "aria-controls": "api-key",
    "aria-pressed": "false",
    onclick: () => {
      const shown = input.type === "password";
      input.type = shown ? "text" : "password";
      reveal.setAttribute("aria-pressed", String(shown));
      reveal.textContent = shown ? "Hide" : "Show";
      input.focus();
    },
  }, "Show");
  const remember = el("input", { type: "checkbox", name: "remember", checked: true });
  const error = el("p", { id: "key-error", class: "error", role: "alert" });
  const submit = el("button", { type: "submit", class: "primary" }, status.configured ? "Replace key" : "Save and continue");

  const box = el("form", {
    class: "panel key-form",
    onsubmit: async (event) => {
      event.preventDefault();
      error.textContent = "";
      submit.disabled = true;
      submit.textContent = "Checking with Anthropic…";
      try {
        await api("/api/key", { method: "POST", body: { api_key: input.value, remember: remember.checked } });
        input.value = "";
        location.assign(next());
      } catch (failure) {
        error.textContent = failure.message;
        submit.disabled = false;
        submit.textContent = status.configured ? "Replace key" : "Save and continue";
        input.focus();
        input.select();
      }
    },
  },
    el("label", { class: "field", for: "api-key" }, el("span", {}, status.configured ? "New API key" : "API key")),
    el("div", { class: "secret" }, input, reveal),
    el("p", { id: "key-help", class: "muted small" },
      "Create one in the ",
      el("a", { href: KEYS_URL, target: "_blank", rel: "noopener noreferrer" }, "Anthropic Console"),
      ". It's checked with Anthropic before it's used, and it's only ever sent to Anthropic."),
    error,
    el("label", { class: "check" }, remember,
      el("span", {}, "Remember this key on this computer",
        el("span", { class: "muted small block" },
          "Saved as a plain file in Incidently's data folder, like a .env file. Untick to be asked again next time the app starts."))),
    el("div", { class: "actions-row" }, submit),
  );
  return { box, input };
}

async function forgetKey(status, origin) {
  const choice = await ask("Forget this key?", [
    status.source === "saved"
      ? "The saved copy is deleted from this computer, and Incidently stops using the key."
      : "Incidently stops using the key.",
    "You'll need to enter a key again before you can play.",
  ], [{ label: "Cancel" }, { label: "Forget key", class: "danger" }], { origin });
  if (choice !== "Forget key") return;
  try {
    render(await api("/api/key", { method: "DELETE" }));
  } catch (failure) {
    root.prepend(errorState("Couldn't forget the key", failure));
  }
}

function render(status) {
  document.querySelector(".nav").hidden = !status.configured;
  if (status.source === "offline") {
    root.replaceChildren(
      el("h1", {}, "No key needed offline"),
      el("p", { class: "lead" },
        "Incidently is running in offline mode (INCIDENTLY_FAKE_LLM=1), so hosts reply with " +
        "placeholder output and no API key is used."),
      el("a", { class: "button primary", href: next() }, "Continue to incidents"),
    );
    return;
  }
  const { box, input } = form(status);
  root.replaceChildren(...[...intro(status), current(status), box].filter(Boolean));
  if (!status.configured) input.focus();
}

async function load() {
  try {
    render(await api("/api/key"));
  } catch (failure) {
    root.replaceChildren(errorState("Couldn't load the key settings", failure, load));
  }
}

initChrome();
load();
