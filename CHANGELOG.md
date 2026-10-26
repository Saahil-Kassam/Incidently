# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Saved sessions (`incidently.storage`): every session is stored in a local SQLite file after
  each command, hint, and submission, so incidents survive a restart and can be resumed. The
  file lives in the per-user data folder, or `INCIDENTLY_DATA_DIR`; offline mode uses its own
  file so placeholder sessions never count as progress. The schema is versioned with
  `PRAGMA user_version`, and a file from a newer version is refused rather than misread.
- Hub progress: each incident shows *In progress*, *Passed*, or *Not passed yet*, the best
  score, and the number of attempts. Opening an incident with an unfinished attempt offers to
  resume it or start a new one.
- Incident history on the hub: every attempt with its start date, time taken, commands, hints,
  and result, linking to its debrief (or back into the session if it isn't finished).
- `GET /api/progress` and `GET /api/history`, built from allowlisted views like the rest of
  the API.
- The start-up message says where sessions are saved; an unusable data folder is reported
  before the server starts.

### Changed
- Sessions are no longer limited to the 50 most recent; the database keeps them all.

## [0.2.0] - 2026-10-26

Command output streams into the terminal as the model writes it.

### Added
- Streaming output: a simulated command's output appears in the terminal as the model writes
  it, instead of all at once when the reply is complete.
- `POST /api/sessions/{id}/commands/stream`: runs a command and answers with newline-delimited
  JSON events (`output`, `restart`, then a final `turn` or `error`). The command runs on its
  own thread, so it finishes and is recorded even if the browser disconnects. The JSON
  `commands` endpoint is unchanged.
- Incremental decoding (`incidently.streaming`): `StdoutDecoder` pulls the `stdout` string out
  of the partial JSON reply, handling escapes and surrogate pairs split across pieces. It stops
  at the end of `stdout`, so the state changes and new facts are never streamed.
- `TextListener` in `incidently.llm`: `generate()` takes an optional listener that receives the
  raw reply text as it arrives and is told to restart on a re-ask or a fallback model taking
  over. The fake model streams its replies in small pieces too.

## [0.1.0] - 2026-10-19

First playable release.

### Added
- Local web app (`python -m incidently`, or the `incidently` command): a scenario hub, a play
  page with an xterm.js terminal (line editing, history, Ctrl+C/Ctrl+L) beside the ticket,
  objective, hosts, hints and submit button, and a debrief page with the score breakdown,
  checkpoints, risky commands, root cause, and an expert command path.
- JSON API (FastAPI) over the engine: list scenarios, start a session, run a command, take a
  hint, finish, and fetch the session or debrief. Model failures become clear HTTP errors.
- No-leak rule: the browser only receives allowlisted fields (`incidently.web.views`); the
  root cause, key evidence, criteria, facts, and notes stay server-side until the debrief.
  A test plays a full session and scans every response for them.
- Local-only protections: requests must use a local host name (DNS rebinding) and
  cross-origin POSTs are refused, since every command spends the user's API credits. One
  request at a time per session (HTTP 409 otherwise).
- xterm.js 5.5.0 and its fit addon vendored with their MIT licences, so the app works offline.
- Session history keeps the prompt each command was typed at and local output, so the
  terminal can be redrawn after a page reload.
- Runtime dependencies: `fastapi` and `uvicorn`.
- Project skeleton: `src/incidently` package, `pyproject.toml`, pytest and ruff config.
- GitHub Actions CI running lint and tests on every supported Python version (3.11 through 3.15).
- Roadmap in `docs/roadmap.md`.
- Scenario schema (`incidently.scenario`): strict Pydantic models that reject unknown keys and
  check hosts, grading criteria, and `{{placeholder}}` declarations when a file loads.
- Scenario catalog (`incidently.catalog`): loads `<id>.yaml` files with clear per-file errors,
  and `instantiate()` produces a seeded, reproducible playthrough with a hidden root cause.
- Four networking scenarios: DHCP pool exhaustion, Wi-Fi 802.1X/RADIUS outage,
  DNS cache poisoning, and an OSPF/EIGRP routing loop.
- Runtime dependencies: `pydantic` and `ruamel.yaml`.
- Settings (`incidently.config`): environment variables with a `.env` fallback (found from the
  working directory), validated with
  errors that name the offending variable. All variables are documented in `.env.example`.
- LLM client (`incidently.llm`): a `StructuredLLM` interface and `AnthropicLLM`, the only code
  that calls the Anthropic API. Replies use structured outputs and are validated against a
  Pydantic model, with re-asks for truncated, empty, or invalid replies. API errors become
  clear `LLMConfigError` / `LLMUnavailableError` / `LLMRefusalError` / `LLMResponseError`
  exceptions, including when no API key is set at all. Refused requests fall back server-side
  on models that support it; results report the serving model, whether a fallback served it,
  and the API request id.
- Fake LLM (`incidently.fake_llm`): a scripted stand-in used by tests and by
  `INCIDENTLY_FAKE_LLM=1`, so no test calls the real API.
- Runtime dependencies: `anthropic` and `python-dotenv`.
- Simulation engine (`incidently.engine`): starts a session with one setup call that writes the
  ticket and hidden facts, then simulates each command with a snapshot of the session state.
  Model failures leave the session unchanged and return a short notice instead of raising.
- Session state (`incidently.session`): hosts, the chain of remote sessions, per-host user,
  directory and Cisco mode, service changes, established facts, and command history. Replies
  are checked against the host inventory. Sessions round-trip through JSON.
- Local command handling (`incidently.shell`): `clear`, `history`, `exit`, Cisco configuration
  mode `end`/`exit`, and refusal of interactive programs, all without an API call; shell
  prompts for bash, PowerShell, and Cisco IOS.
- Grading (`incidently.grading`): one grading call decides which criteria were met and writes
  the debrief; the score (criterion points, +10 evidence bonus, -5 per hint, -5/-15 per
  high-risk/destructive command, floored at 0) and pass rule are computed locally. Claims are
  checked against the command log: a criterion needs a cited, simulated command to count.
- Hints: `Engine.hint()` has the model write each hint for the player's progress, falling
  back to the scenario's own hint if the call fails or the hint quotes the hidden answer.
- `Engine.finish()` ends a session, grades it once, and returns the debrief; failed grading
  can be retried. Ended sessions accept no more commands or hints.
- Offline play: `INCIDENTLY_FAKE_LLM=1` now runs a full session with placeholder output
  (`incidently.offline`), and `FakeLLM` takes a `default` reply for unscripted requests.

### Changed
- CI uses `actions/checkout@v7` and `actions/setup-python@v7`, which run on Node.js 24 instead of the deprecated Node.js 20.
