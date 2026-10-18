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
src/incidently/     application package
scenarios/          incident blueprints (YAML)
tests/              pytest suite (never calls the real API)
docs/               roadmap and design notes
```

## Contributing

Work happens on feature branches merged into `main` through pull requests.
Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
(`feat:`, `fix:`, `docs:`, `test:`, `chore:`). Every change updates
`CHANGELOG.md`.
