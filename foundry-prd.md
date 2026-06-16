# Foundry PRD — Product Requirements Document

Date: 2026-06-16  
Status: Draft

---

## Problem

AI-assisted development produces code faster than quality checks can keep up with. Developers and agents need a cheap, local way to ask: "Is this change acceptable according to this repo's gates, and what evidence supports that answer?"

Existing tools solve pieces of this:
- `act` runs GitHub Actions locally but has no evidence contract, scheduling, or decision layer
- Make/just/Task run commands but produce no structured output
- CI systems (GitHub Actions, Buildkite) are cloud-dependent, expensive for routine checks, and not agent-readable by default

No single tool provides: **local repo-defined quality gates + Act/GHA compatibility + scheduled profiles + artifact capture + structured agent-readable decision output**.

---

## User

**Primary:** A solo developer or small team using AI coding tools (Claude, Codex, Aider) to write and iterate on code. They want quality checks to run locally before committing, without paying cloud CI per-run or waiting for remote feedback loops.

**Secondary:** An agent pipeline (PAS, Reckoner) that needs a reliable gate between steps. The agent calls `foundry run --json`, reads `result.json`, and decides whether to continue, retry, or stop.

---

## Goals

1. Run repo-defined quality gates locally (lint, test, build, Act workflows)
2. Produce stable, machine-readable evidence that agents can trust
3. Schedule profiles to run without manual invocation
4. Classify failures into actionable decisions

---

## Non-Goals

Foundry is **not**:
- A new CI system
- A GitHub Actions replacement or clone
- A Dagger competitor
- A workflow UI (n8n)
- A repo initializer
- A PAS/Reckoner orchestrator
- A dashboard-first product
- An issue tracker

---

## User Stories

### Core

- As a developer, I want to run `foundry run quick` before committing so I catch lint/test failures locally before pushing.
- As a developer, I want `foundry doctor` to tell me if my environment (Docker, Act, git) is ready before I start.
- As an agent, I want to call `foundry run quick --json` and read a `result.json` that tells me whether to continue or stop.
- As a developer, I want failed gate output captured in a log file I can open, not buried in terminal scrollback.

### Scheduling

- As a developer, I want to run `foundry schedule install nightly` so a full gate profile runs at 2am without manual invocation.
- As a developer, I want scheduled runs to produce the same `result.json` format as manual runs so I can read both the same way.

### Reporting

- As a developer, I want `foundry latest --summary` to print a human-readable summary of the last run.
- As an agent, I want the `result.json` to include `agent_recommendation.safe_to_continue` so I don't need to interpret raw gate results.

---

## Functional Requirements

### F1 — `foundry doctor`

Validates local environment and repo config before a run. Checks:
- git: repo detected, no uncommitted risk
- Docker or Colima: running and accessible
- Act: binary on PATH and functional
- `foundry.yaml`: present and parseable
- Repo runner detection: Makefile / justfile / Taskfile / mise tasks
- `.github/workflows/`: workflow files found if Act gates are configured

Exits 0 if all required checks pass; non-zero if any required check fails. Prints per-check status to stdout.

### F2 — `foundry run <profile>`

Reads `foundry.yaml`. Runs gates defined in the specified profile **sequentially**. For each gate:
- shell/make/just/task/mise: subprocess execution
- act: invokes `act` CLI with configured workflow and event

Gate execution:
- Streams stdout/stderr to a per-gate log file in `.foundry/runs/<run-id>/logs/<gate-id>.log`
- Records start time, end time, exit code
- Applies `allow_failure` and `timeout` per gate
- Sets gate status: `passed / failed / skipped / timed_out`

On completion:
- Writes `.foundry/runs/<run-id>/result.json`
- Writes `.foundry/runs/<run-id>/summary.md`
- Exits with code 0 (decision: continue) or 1 (decision: stop/needs_human)

With `--json` flag: writes `result.json` content to stdout in addition to the file.

### F3 — Output contract

Every run produces:
```
.foundry/runs/<run-id>/
  result.json       # machine-readable, schema_version: 1
  summary.md        # human-readable markdown
  logs/
    <gate-id>.log   # raw stdout+stderr from each gate
  metadata.json     # run metadata (foundry version, env)
```

`<run-id>` format: `YYYY-MM-DDTHH-MM-SSZ`

### F4 — Decision model

Each run produces exactly one top-level decision:

| Decision | Meaning | Exit code |
|----------|---------|-----------|
| `pass` | All required gates passed | 0 |
| `warn` | Required gates passed; some `allow_failure` gates failed | 0 |
| `fail` | One or more required gates failed | 1 |
| `needs_human` | A gate returned a signal requiring human review | 1 |

`agent_recommendation` is derived from the decision:
- `pass` or `warn` → `safe_to_continue: true`
- `fail` or `needs_human` → `safe_to_continue: false`

There is no separate `retry` or `stop` decision in v1. Consumers decide whether to retry based on `fail`.

### F5 — Scheduling

`foundry schedule install <schedule-name>`:
- Reads schedule definition from `foundry.yaml`
- Installs a cron entry (Linux) or launchd plist (macOS) that calls `foundry run <profile>`
- Scheduled runs produce identical evidence to manual runs

`foundry schedule list`: prints installed schedules and their cron expressions.

`foundry schedule remove <schedule-name>`: uninstalls the entry.

No daemon. Scheduling is delegated to the OS.

### F6 — `foundry latest`

Reads the most recent run directory. With `--summary`: prints `summary.md` content to stdout. With `--json`: prints `result.json` to stdout.

---

## Non-Functional Requirements

- **Single binary / no runtime dependency on Foundry itself**: target repos should only need `foundry` on PATH, plus their own configured runners (Make, Act, etc.)
- **Cold start < 500ms** for `foundry doctor` and `foundry latest` (no gate execution)
- **Log size cap**: truncate gate logs at 500KB; record truncation in metadata
- **No network calls** during gate execution except through configured gates (Act may pull images)
- **Portable output**: `result.json` must be readable without Foundry installed

---

## Out of Scope for MVP

- `foundry explain` (LLM gate failure explanation) — Phase 2
- PAS/Reckoner integration beyond `--json` output — Phase 2
- Beads issue creation on failure — Phase 3
- Parallel gate execution — Phase 2
- Dagger runner backend — Phase 2
- Web/TUI dashboard — not planned
- `foundry init` / repo bootstrapping — owned by `scott-cc .init`

---

## Success Metrics

- Can run `foundry run quick` on an existing repo with a Makefile in under 5 minutes of setup
- `result.json` is parseable by `jq` with zero configuration
- A PAS/Reckoner agent can consume `foundry run --json` output and make a continue/stop decision without reading logs
- Scheduled nightly run fires and produces evidence without manual intervention
