# Incidently

A local hub for practising enterprise networking incident response.

Pick an incident from the hub, get a ticket, and troubleshoot simulated hosts
(Linux, Cisco IOS, PowerShell) in a browser terminal. Claude generates realistic
output for every command and keeps the environment consistent as you change it.
When you submit, you get a score and a debrief explaining the real root cause.

> **Status:** early development. Nothing is playable yet. See the
> [roadmap](docs/roadmap.md) for what's planned and in what order.

## Requirements

- Python 3.11 or newer (tested on 3.11 through 3.15)
- An Anthropic API key (needed once the simulator is playable)

## Development setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env    # then add your ANTHROPIC_API_KEY
```

Run the checks:

```powershell
ruff check .
ruff format --check .
pytest
```

## Project layout

```
src/incidently/             application package
  scenario.py               scenario schema (Pydantic) and validation rules
  catalog.py                loads scenario files; randomises a playthrough
  config.py                 settings from environment variables / .env
  llm.py                    the only code that calls the Anthropic API
  fake_llm.py               scripted stand-in model for tests and offline use
  offline.py                placeholder replies for offline play
  replies.py                reply shapes the model must produce (ticket, command output)
  session.py                state of one playthrough: hosts, history, facts
  shell.py                  built-ins handled locally; shell prompts
  prompts.py                prompt text for setup and simulation calls
  engine.py                 runs a playthrough: setup, then one call per command
  scenarios/*.yaml          bundled incident scenarios
tests/                      pytest suite (never calls the real API)
docs/                       roadmap and design notes
```

## Scenarios

| id | Topic | Level |
|---|---|---|
| `dhcp-pool-exhaustion` | DHCP | beginner |
| `wifi-8021x-radius` | Wireless (802.1X) | beginner |
| `dns-cache-poisoning` | DNS | intermediate |
| `ospf-routing-loop` | Routing | advanced |

Each scenario is a YAML file named after its `id`. Every file is validated
when the catalog loads, and an invalid one stops the app with an error naming
the file. Each playthrough is randomised from a single seed: `{{placeholders}}`
such as site names and subnets are filled in, and one of several hidden root
causes is chosen, so the same ticket can have a different cause each time.
The same seed always reproduces the same playthrough.

## Configuration

Settings are environment variables, read from a local `.env` file if present.
`.env.example` lists them all with their defaults.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | Your API key (not needed with the fake model) |
| `INCIDENTLY_MODEL` | `claude-opus-5-5` | Model for every call |
| `INCIDENTLY_TURN_EFFORT` | `low` | Effort per simulated command; favours speed |
| `INCIDENTLY_DEEP_EFFORT` | `high` | Effort for setup and grading; favours quality |
| `INCIDENTLY_FAKE_LLM` | `0` | `1` plays offline with placeholder output and never calls the API |
| `INCIDENTLY_API_MAX_RETRIES` | `4` | Retries for rate limits, 5xx errors, and network drops |
| `INCIDENTLY_API_TIMEOUT` | `120` | Seconds per API request |

### How a playthrough runs

1. **Setup.** One call at `INCIDENTLY_DEEP_EFFORT` turns the randomised scenario
   into a service-desk ticket and a list of hidden facts (log lines, versions,
   addresses) that keep the simulation consistent.
2. **Commands.** Each command goes to the model with a snapshot of the session
   state: the active host, the chain of remote sessions, each host's user,
   directory and Cisco mode, service changes, and every fact established so
   far. The reply is the output plus state changes, which the engine applies
   and checks against the host inventory. The model sees the last 40 commands;
   older ones are dropped in chunks, and the facts list carries continuity.
3. **Handled locally, with no API call:** `clear`, `history`, `exit` (leaves a
   remote session; on the first host it asks to end the incident), Cisco
   `end`/`exit` in configuration mode, and interactive programs such as `vim`,
   `less`, `top` and `tail -f`, which are refused with a non-interactive
   alternative.

If a command can't be simulated (API down, refusal, bad reply), the player
sees a short notice and the session is left exactly as it was.

### How model calls work

Every call asks for a specific Pydantic model and uses structured outputs, so
the reply must match its JSON schema. The reply is then validated locally. If
it is cut off, the request is retried with a larger token limit; if it is empty
or invalid, it is retried (three attempts in total). Rate limits, server errors,
and network drops are retried by the Anthropic SDK with exponential backoff. If
a safety classifier declines a request, the API re-runs it on a fallback model
(`fallbacks: "default"`). When that happens the result reports the model that
actually served it (`LLMResult.model`, `LLMResult.fell_back`), and the log line
for the call shows `fallback=True`.

Host simulation prompts can trip the classifier: in testing,
`claude-opus-5-5` requests were served by `claude-opus-4-8`. The game still
works; set `INCIDENTLY_MODEL` to pick a different model.

`.env` is looked up from the folder you start the app in (and its parents).

## Contributing

Work happens on feature branches merged into `main` through pull requests.
Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
(`feat:`, `fix:`, `docs:`, `test:`, `chore:`). Every change updates
`CHANGELOG.md`.
