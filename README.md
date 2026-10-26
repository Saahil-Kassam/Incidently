# Incidently

A local hub for practising enterprise networking incident response.

Pick an incident from the hub, get a ticket, and troubleshoot simulated hosts
(Linux, Cisco IOS, PowerShell) in a browser terminal. Claude generates realistic
output for every command and keeps the environment consistent as you change it.
When you submit, you get a score and a debrief explaining the real root cause.

> **Status:** v0.3.0: four networking incidents, played in the browser with
> output streaming in as it is generated, graded at the end, and saved so you
> can resume them and track your progress. See the
> [roadmap](docs/roadmap.md) for what comes next.

## Requirements

- Python 3.11 or newer (tested on 3.11 through 3.15)
- An Anthropic API key, or offline mode to try the app with placeholder output

## Running Incidently

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e .
copy .env.example .env    # then set ANTHROPIC_API_KEY in .env (remove the leading #)
python -m incidently
```

This starts the app at <http://127.0.0.1:8000> and opens it in your browser.
`incidently` works too, once the virtual environment is active. Options:
`--port 8080`, `--no-browser`. Press Ctrl+C in the terminal to stop it.

To look around without an API key or spending credits, run it in offline
mode, where hosts reply with placeholder output and nothing is graded:

```powershell
$env:INCIDENTLY_FAKE_LLM = "1"; python -m incidently
```

### Playing

1. **Pick an incident** on the hub. Search by name, topic, or skill, and
   filter by difficulty or by your status (not started, in progress, passed,
   not passed yet). Each incident has its own page with a description, the
   skills it practises, and your attempts; start or resume from there. Setup
   takes up to a minute while the ticket and the hidden details of the fault
   are generated.
2. **Investigate** in the terminal with real commands for each host's shell
   (bash, PowerShell, or Cisco IOS). `ssh` to other hosts in the inventory;
   `exit` brings you back. Arrow keys recall history, Ctrl+C clears the line.
   Editors and pagers aren't available, so use `sed -i`, `tee`, `cat`, `grep`.
3. **Take hints** from the side panel if you're stuck (−5 points each).
4. **Submit** when you think it's fixed (or type `exit` on the first host).
   Grading takes up to a minute; the debrief shows your score, each checkpoint,
   risky commands, the real root cause, and how an expert would have solved it.

Every session is saved as you play, so you can close the app and pick up
where you left off: the hub marks each incident *In progress*, *Passed*, or
*Not passed yet* with your best score, and an incident's page offers to resume
an unfinished attempt. **Progress** shows incidents passed, pass rate,
average score, time spent, which skills you've proven, and every attempt with
its date, time taken, and result. Graded attempts reopen their debrief.

The app follows your system's light or dark setting; the button in the top
bar switches between System, Light, and Dark. Press <kbd>?</kbd> for keyboard
shortcuts: <kbd>/</kbd> searches, <kbd>g</kbd> <kbd>i</kbd> and
<kbd>g</kbd> <kbd>p</kbd> go to Incidents and Progress, and <kbd>Esc</kbd>
closes a dialog or clears the search. Reduced motion, reduced transparency,
and increased contrast settings are respected.

Sessions are saved in a SQLite file in your
user data folder (`%LOCALAPPDATA%\Incidently` on Windows; see
`INCIDENTLY_DATA_DIR` below), and offline mode keeps a separate file, so
placeholder sessions never count towards your progress.

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
  streaming.py              decodes a command's stdout while the reply streams in
  grading.py                scoring rules and the debrief
  storage.py                saved sessions (SQLite), history and progress
  __main__.py               `python -m incidently`: starts the server
  web/app.py                FastAPI app: JSON API, streamed commands, pages, request checks
  web/views.py              the only shapes the browser receives
  web/static/               hub, scenario, progress, play, and debrief pages (HTML + ES modules)
  web/static/vendor/xterm/  xterm.js 5.5.0 + fit addon, vendored (MIT)
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
| `INCIDENTLY_DATA_DIR` | per-user data folder | Where sessions are saved: `incidently.db`, and `incidently-offline.db` for offline mode. Defaults to `%LOCALAPPDATA%\Incidently` (Windows), `~/Library/Application Support/Incidently` (macOS), or `~/.local/share/incidently` |

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
   Output appears in the terminal as the model writes it: `stdout` is the
   reply's first field, so it is decoded from the partial JSON and streamed to
   the browser before the rest of the reply (the state changes, which can name
   hidden details) has been written. Those are never streamed.
3. **Handled locally, with no API call:** `clear`, `history`, `exit` (leaves a
   remote session; on the first host it asks to end the incident), Cisco
   `end`/`exit` in configuration mode, and interactive programs such as `vim`,
   `less`, `top` and `tail -f`, which are refused with a non-interactive
   alternative.

If a command can't be simulated (API down, refusal, bad reply), the player
sees a short notice and the session is left exactly as it was.

4. **Hints.** Each scenario has a fixed number of hints. The model writes each
   one for where the player actually is, from the command log and the hints
   already given. If that call fails, or the hint quotes the hidden answer
   word for word, the scenario's own hint is shown instead.
5. **Grading.** Submitting ends the session and makes one call at
   `INCIDENTLY_DEEP_EFFORT` over the whole command log. The model decides which
   criteria were met (citing the command that met each), whether the incident
   was resolved, and which commands were risky, and writes the debrief. The
   score itself is calculated locally:

   | | Points |
   |---|---|
   | Each criterion met | its points (each scenario totals 100) |
   | Key evidence uncovered | +10 |
   | Each hint used | -5 |
   | Each high-risk command | -5 |
   | Each destructive command | -15 |

   The score never goes below 0. **Passing** needs the incident resolved and
   every required criterion met; otherwise the grade is at best a D. Criteria
   the model claims without citing a real simulated command don't count.

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

Every call streams. Simulated commands pass a listener that hears the reply
text as it arrives; if an attempt is retried or a fallback model takes over,
the listener is told to start again and the terminal notes that the output
was cut off before showing the new one.

Host simulation prompts can trip the classifier: in testing,
`claude-opus-5-5` requests were served by `claude-opus-4-8`. The game still
works; set `INCIDENTLY_MODEL` to pick a different model.

`.env` is looked up from the folder you start the app in (and its parents).

## Contributing

Work happens on feature branches merged into `main` through pull requests.
Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
(`feat:`, `fix:`, `docs:`, `test:`, `chore:`). Every change updates
`CHANGELOG.md`.
