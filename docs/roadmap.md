# Incidently Roadmap

## Context
Incidently is a **local** networking scenario hub: a browser app on `localhost` where a player picks an
enterprise networking incident, troubleshoots simulated hosts (Linux, Cisco IOS, PowerShell) through a
terminal whose output Claude generates, then submits for a score and debrief. It is a fresh rewrite
inspired by NetSim (`../NetSim`). NetSim's code is reference only; its scenario YAML content is reused.

Decisions:
- Local web app (FastAPI + browser UI), no terminal-first build.
- Rewrite from scratch; reuse NetSim scenarios as data.
- Networking scenarios first (DHCP, Wi-Fi 802.1X, DNS, OSPF loop); the other 4 come later as "Enterprise Systems".
- No accounts: single local user, `ANTHROPIC_API_KEY` in `.env`, progress in local SQLite.

## Working practices (apply to every milestone)
- `main` is always working. Each milestone = a feature branch → PR → merge (squash). Small milestones may be 2–3 PRs.
- Conventional commit messages (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Every PR: tests pass in CI, README/CHANGELOG updated, no secrets (`.env` ignored).
- Tag releases with SemVer: `v0.1.0` = first playable build, then minor bumps per user-visible feature.
- Tests never call the real API (fake LLM).

## Stack
Python 3.11+, `src/incidently/` layout, `pyproject.toml`, Pydantic v2, PyYAML, `anthropic` SDK,
tenacity, python-dotenv, FastAPI + uvicorn, SQLAlchemy + SQLite, plain HTML/ES modules + xterm.js
(no JS build step), pytest + ruff, GitHub Actions.

## Features, ordered by necessity

### Milestone 0 — Repository foundation (push #1)
- **Project skeleton**: `pyproject.toml`, `src/incidently/__init__.py`, `tests/`, `.env.example`, ruff + pytest config.
- **README v1**: what Incidently is, setup, roadmap link.
- **CI**: GitHub Actions running ruff + pytest on push/PR.
- **Repo hygiene**: `.gitignore` (already present), LICENSE (user to choose), CHANGELOG.md.
- **Create GitHub repo** with `gh repo create` and push `main`.

### Milestone 1 — Scenario format & loader (core, no UI)
- **Scenario schema**: Pydantic models for a scenario (id, title, category, difficulty, objective, hosts, root-cause variants, hidden evidence, resolution condition, grading criteria, hints, simulation notes). Modelled on NetSim's `ScenarioSpec` in `NetSim/netsim/models.py`.
- **Loader + validation**: load `scenarios/*.yaml`, fail fast with clear errors on bad files.
- **Randomiser**: fill `{{placeholders}}` and pick a hidden root-cause variant; optional seed for reproducibility.
- **Networking scenarios**: copy the 4 networking YAMLs from `NetSim/scenarios/` (06 wifi, 07 dns, 08 dhcp, 02 ospf), adapted to the schema.
- *As built:* scenarios ship inside the package (`src/incidently/scenarios/`) so they work from any install, and YAML is parsed with `ruamel.yaml` because PyYAML has no Python 3.15 wheels.
- **Content tests**: every scenario validates; no placeholders left unfilled.

### Milestone 2 — LLM client
- **Anthropic client module**: the only place that calls the API; key from `.env`; model/effort configurable.
- **Strict JSON replies**: structured outputs from Pydantic schemas (`CommandResult`, etc.), validate and re-ask on bad/truncated replies.
- **Resilience**: tenacity retries with backoff for 429/5xx/529/timeouts; clear error on bad key.
- **Fake LLM**: scripted stand-in for tests and offline UI work (`INCIDENTLY_FAKE_LLM=1`).
- *As built:* transient API errors use the Anthropic SDK's built-in retries with backoff (configurable via `INCIDENTLY_API_MAX_RETRIES`) instead of tenacity, so there's one retry layer, not two. Refusals use the API's server-side fallback. The live smoke test (one `hostname -I` call, about 370 tokens) returned valid output, but `claude-opus-5-5` handed the request to `claude-opus-4-8`; results now flag this (`fell_back`). Check whether this repeats for the real simulation prompt in Milestone 3. The fake LLM is scripted per test; Milestone 3 adds default scripts for offline play.

### Milestone 3 — Simulation engine
- **Session state**: hosts, active host / SSH chain, user, cwd, IOS mode, service changes, invented facts, command history.
- **Local command handling**: `clear`, `history`, `exit`, and blocking interactive tools (`vim`, `top`…) without an API call.
- **Orchestrator**: command → prompt with state snapshot → `CommandResult` → apply state updates. UI-agnostic; returns data, never prints.
- **Environment bootstrap**: generate the incident ticket and starting state when a session begins.

### Milestone 4 — Grading, hints & debrief
- **Final assessment**: one grading call over the full command log decides which criteria were met.
- **Deterministic scoring**: arithmetic done locally (criterion points, evidence bonus, hint and risky/destructive-action penalties, pass rule).
- **Hints**: progressive hints with a point cost.
- **Debrief data**: what you found, what you missed, the real root cause, recommended fix.

### Milestone 5 — Local web app MVP → release `v0.1.0` (first playable)
- **FastAPI backend**: start session, send command, hint, submit, get debrief. Sessions held in memory.
- **Scenario hub page**: card grid of scenarios (title, category, difficulty, summary).
- **Play page**: xterm.js terminal, ticket/objective panel, hint and submit buttons.
- **Debrief page**: score, criteria met/missed, explanation.
- **No-leak rule**: API responses only contain allowlisted fields; root cause and rubric never reach the browser.
- **One-command start**: `python -m incidently` launches the server and opens the browser.

### Milestone 6 — Streaming output (`v0.2.0`)
- **WebSocket command channel**: stdout appears as Claude generates it instead of after the full reply.
- **Incremental JSON decoding** of the streamed `stdout` field.

### Milestone 7 — Persistence & progress (`v0.3.0`)
- **SQLite storage**: sessions survive a server restart; resume an unfinished incident.
- **Incident history**: past attempts with score, date, duration; reopen any debrief.
- **Hub progress badges**: not started / in progress / passed, best score per scenario.

### Milestone 8 — Hub polish (`v0.4.0`)
- **Filter & search**: by category, difficulty, status; text search.
- **Scenario detail page**: description, skills practised, your attempts.
- **Progress dashboard**: totals, pass rate, skills breakdown.
- **UX polish**: light/dark theme, keyboard shortcuts, responsive layout, error states.

### Milestone 9 — Enterprise Systems scenarios (`v0.5.0`)
- **Port remaining 4 NetSim scenarios** (AD sync, SELinux, SQLite corruption, iOS MDM) under an "Enterprise Systems" category, filterable in the hub.
- **Scenario authoring guide**: `docs/writing-scenarios.md` + a `validate` CLI command.

### Milestone 10 — Cost visibility & efficiency (`v0.6.0`)
- **Prompt caching**: fixed per-session system prompt; append-only history trimmed in chunks.
- **Usage panel**: tokens and estimated cost per session and overall.

### Milestone 11 — AI-generated scenarios (`v0.7.0`)
- **Generate a scenario from a theme** (e.g. "BGP flap", "VLAN mismatch"), validated against the schema, saved to a local "Custom" category.

### Later / optional backlog (lowest necessity)
- Network topology diagram per scenario.
- Export debrief as PDF/Markdown.
- Achievements / streaks.
- Docker image for one-step setup.
- Hosted multi-user version (would reintroduce auth, rate limits, deploy hardening — NetSim's `docs/deploy-runbook.md` is the reference).


## Verification (per milestone)
- `ruff check` and `python -m pytest` pass locally and in CI.
- M1: all scenarios load and randomise with a fixed seed reproducibly.
- M2–M4: unit tests with the fake LLM; one manual smoke run against the real API.
- M5+: `python -m incidently`, open the hub, play a scenario end to end (fake LLM, then real), confirm the debrief and that no hidden fields appear in browser network responses.
