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

## Contributing

Work happens on feature branches merged into `main` through pull requests.
Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
(`feat:`, `fix:`, `docs:`, `test:`, `chore:`). Every change updates
`CHANGELOG.md`.
