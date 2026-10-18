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
| `INCIDENTLY_FAKE_LLM` | `0` | `1` uses a scripted fake model and never calls the API |
| `INCIDENTLY_API_MAX_RETRIES` | `4` | Retries for rate limits, 5xx errors, and network drops |
| `INCIDENTLY_API_TIMEOUT` | `120` | Seconds per API request |

### How model calls work

Every call asks for a specific Pydantic model and uses structured outputs, so
the reply must match its JSON schema. The reply is then validated locally. If
it is cut off, the request is retried with a larger token limit; if it is empty
or invalid, it is retried (three attempts in total). Rate limits, server errors,
and network drops are retried by the Anthropic SDK with exponential backoff. If
a safety classifier declines a request, the API re-runs it on a fallback model
(`fallbacks: "default"`).

## Contributing

Work happens on feature branches merged into `main` through pull requests.
Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
(`feat:`, `fix:`, `docs:`, `test:`, `chore:`). Every change updates
`CHANGELOG.md`.
