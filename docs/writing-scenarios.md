# Writing scenarios

A scenario is one YAML file describing an incident: the ticket's subject, the hosts the player can
log in to, a few possible hidden root causes, and how the work is graded. Claude does the rest. It
writes the ticket and the concrete details of the fault at the start, then plays every host as
the player types. Your job is to give it a precise, consistent blueprint.

This guide covers the format, the rules `incidently validate` enforces, and what makes a scenario
good to play.

## Quick start

1. Copy a bundled scenario from `src/incidently/scenarios/` that is close to yours, for example
   `wifi-8021x-radius.yaml` for a single Linux host or `ad-sync-failure.yaml` for two PowerShell
   hosts.
2. Rename it to `<your-id>.yaml` and set `id` to match. Ids are lowercase words joined by hyphens.
3. Edit every field. The sections below explain each one.
4. Check it:

   ```powershell
   python -m incidently validate src/incidently/scenarios/your-id.yaml
   ```

5. Play it against the real model. Offline mode (`INCIDENTLY_FAKE_LLM=1`) only proves that the
   file loads and the hub shows it; the hosts reply with placeholder text.

## What the player sees, and what stays hidden

Getting this split right is the most important part of a scenario. Hidden fields go to the model
only, and the server's no-leak rule keeps them out of the browser until the debrief.

| Field | Who sees it | When |
|---|---|---|
| `title`, `category`, `topic`, `difficulty`, `summary`, `skills` | Player | Hub card and scenario page |
| `description` | Player | Scenario page, before starting |
| `objective`, `hosts` (name, os, role, ip) | Player | During play |
| `hints` | Player | One at a time, each costing 5 points |
| `grading[].description` | Player | Only in the debrief |
| `root_cause_variants` | Model only | The chosen one is revealed in the debrief |
| `hidden_flag`, `resolution_condition`, `simulation_notes` | Model only | Never shown |

## Fields

```yaml
id: dns-cache-poisoning           # = the file name; lowercase-with-hyphens
title: Intranet Portal Resolves to the Wrong Server
category: Networking              # hub filter: Networking, Enterprise Systems, ...
topic: DNS                        # short subject shown on the card
difficulty: intermediate          # beginner | intermediate | advanced
summary: One line for the hub card.
description: >                    # a few sentences for the scenario page
  ...
skills: [DNS, Reproducing a fault, Configuration review, Hardening]

randomize:                        # placeholder -> possible values, one picked per playthrough
  domain: [corp.local, fabrikam.internal]

objective: >                      # what "done" means, in the player's terms
  Restore correct DNS resolution for portal.{{domain}} ...

hosts:                            # at least one
  - name: dns01                   # short hostname; unique
    os: Ubuntu Server 24.04 LTS, BIND 9.18
    shell: bash                   # bash | powershell | cisco-ios
    role: Internal recursive resolver for {{domain}}
    ip: 10.10.0.53
    user: netadmin                # who the player is logged in as
    cwd: /home/netadmin           # "" for network devices
start_host: dns01                 # where the terminal opens

root_cause_variants: [...]        # at least 2; one is chosen per playthrough
hidden_flag: >                    # the evidence that proves the cause
resolution_condition: >           # what must be true for the incident to count as fixed
grading: [...]                    # criteria; points total 100
hints: [...]                      # vague to specific; at least 2
simulation_notes: >               # realism guidance for the model
```

Unknown keys are rejected, so a typo fails loudly instead of being ignored.

### `summary` and `description`

The summary is the symptom in one line, the way a service desk would phrase it. The description
adds the situation and what the player has to work with ("You have a shell on the internal BIND
resolver"), and what a good outcome looks like. Neither may contain `{{placeholders}}`: they're
shown before a playthrough is randomised.

**Never hint at the cause.** The player should know where the pain is, not why. `validate` warns
if the description repeats five words in a row from a root cause or the hidden flag. Treat that
as a backstop and avoid paraphrased giveaways too.

### `skills`

List two to five. Prefer names other scenarios already use, because the Progress page groups
results by skill. Current vocabulary:

- **Shared:** Service triage, Log analysis, Configuration review, Change control, Reproducing a
  fault, Path tracing, Hardening, Data recovery, Scoping impact, PowerShell
- **Topics:** DHCP, DNS, Routing, Cisco IOS, 802.1X and RADIUS, Active Directory and Entra ID,
  SELinux, Databases, Intune and Apple MDM

A skill must not give away a root cause: "Service triage" is fine, but "Certificate expiry" on a
scenario where one variant is an expired certificate is not.

### `randomize` and placeholders

`{{name}}` in any text field is replaced from `randomize` when a playthrough starts. Every
placeholder must be declared, every declared key must be used, and values in a list must be
unique. One seed picks both the values and the root cause, so any session can be replayed
exactly.

Use placeholders for things that should vary between replays (site names, subnets, usernames),
not for anything the grading depends on. Quote values YAML would read as numbers:
`branch: ["10.44", "10.61"]`.

### `hosts`

Give the model enough to be consistent: the OS and versions, the software that matters, and in
`role`, what the host does and how it relates to the others. The player can `ssh` (or
`Enter-PSSession`) only to hosts listed here.

`shell` decides the prompt and some local behaviour: `bash` shows `user@host:dir$` (with `#` for
root) and resolves relative `cd` paths, `powershell` shows `PS <dir>>`, and `cisco-ios` shows
`host#` and tracks the configuration mode (`exec`, `config`, `config-if`, ...), so `end` and
`exit` work without a model call.

### `root_cause_variants`

Two or more, each a different fault that produces the same symptom, so a replay can't be won from
memory. Write each one as a self-contained brief for the model:

1. **What broke and how**, with specific paths, names, versions and times.
2. **The symptoms and log lines** it produces, quoted the way the real software prints them.
3. **The correct fix**, including the check that proves it.

Each variant must be discoverable from the hosts with ordinary commands. If the evidence lives on
a system the player can't reach, the scenario can't be solved fairly.

### `hidden_flag` and `resolution_condition`

`hidden_flag` names the single piece of evidence that proves the cause, for each variant. The
grader uses it to decide when the player found the cause; hints are checked so they never quote
it.

`resolution_condition` is what must be true at the end: the root cause corrected (not worked
around), the service healthy, and a verification step done.

### `grading`

Criteria the grading call judges from the command log. Rules:

- Points total **100** (a warning otherwise). The key-evidence bonus adds 10 on top.
- At least one criterion is `required: true`. The incident only passes if every required one is
  met and the incident is resolved.
- Ids are `snake_case` and unique.
- Write each description as an observable action ("Find the AVC denial in the audit log"), so the
  grader can point at the command that met it. A criterion only counts if it cites a real
  simulated command.
- Don't reward symptom-fixes. If a shortcut makes the symptom go away but leaves the cause (or
  weakens security), say so in the criterion: "Fix labels with restorecon / semanage (not
  chmod 777, not setenforce 0)".

A typical shape is: triage (10–15), gather evidence (10–20), identify the root cause (20–25,
required), apply the fix (20–30, required), verify (15–20, required).

### `hints`

At least two, from vague to specific. The first points at a place to look, the last names the
tools. None should name the cause: the model rewrites hints for where the player actually is, and
falls back to yours if its version would leak the answer. Each hint costs the player 5 points.

### `simulation_notes`

Everything the model needs to keep the world believable:

- **Timeline:** when the fault started, the last time things worked.
- **Environment facts:** credentials the player legitimately has, backup locations, data sizes.
- **Commands that would block** (`freeradius -X`, `tail -f`): say how to treat them, for example as
  30 seconds of captured output.
- **Destructive shortcuts** to flag, and how the grader should treat them (`setenforce 0`, deleting
  the database, switching the SSID to an open network).
- **Output style:** the formats real tools use (audit.log AVC lines, Graph objects, `show run`).

## Checking a scenario

```powershell
python -m incidently validate                      # the bundled scenarios
python -m incidently validate my-scenario.yaml     # one file
python -m incidently validate drafts/              # every *.yaml in a folder
python -m incidently validate --strict drafts/     # warnings fail too
```

Each file gets one line, followed by any problems:

```
ok     dns-cache-poisoning.yaml: Networking / intermediate, 1 host(s), 3 root causes
warn   my-scenario.yaml: grading points total 90, not 100
error  broken.yaml: id 'broken-thing' must match the file name
3 file(s): 1 with errors, 1 with warnings
```

- **Errors** mean the app would refuse to start with the file: YAML that doesn't parse, schema
  violations, an id that doesn't match the file name or that another file already uses, or a
  playthrough that doesn't resolve. The exit code is 1.
- **Warnings** are house-style problems: the file works, but it's a weaker scenario. They exit 0
  unless you pass `--strict`.

Bundled scenarios must pass with no warnings; the test suite runs the same checks.

## Checklist

- [ ] The id matches the file name, and `validate --strict` passes.
- [ ] Summary, description, and skills tell the player where it hurts, not why.
- [ ] Two or more root causes, each producing the same symptom and each findable from the hosts.
- [ ] Each variant includes real log lines and a fix with a verification step.
- [ ] Grading totals 100, the fix and verification are required, and shortcuts are called out.
- [ ] Hints go from vague to specific and never name the cause.
- [ ] Simulation notes cover timing, blocking commands, and destructive shortcuts.
- [ ] Played start to finish against the real model, including one wrong turn.
