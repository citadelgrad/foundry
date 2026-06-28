# Foundry

A gate-based CI pipeline runner for local and scheduled workflows. Define quality gates in `foundry.yaml`, run them locally, and schedule them as recurring checks — no external CI required.

## Install

```bash
pip install foundry-lite   # or: uv add foundry
```

Or from source:

```bash
cd foundry-lite
uv sync
uv run foundry --help
```

## Quick start

```bash
foundry init          # scaffold foundry.yaml in current repo
foundry doctor        # verify config and dependencies
foundry run default   # run the default profile
foundry latest        # show result of last run
```

## foundry.yaml

```yaml
version: 1

profiles:
  default:
    gates:
      - id: lint
        run: ruff check .
      - id: test
        run: pytest
        timeout: 5m

schedules:
  nightly:
    profile: default
    cron: '0 2 * * *'

integrations:
  explain:
    on_failure: true
    model: claude-sonnet-4-6
```

## Gates

Each gate runs a shell command. Gates are **fail-fast** by default — first failure stops the profile.

| Field | Default | Description |
|-------|---------|-------------|
| `run` | — | Shell command to execute |
| `timeout` | `10m` | Max runtime (`s`, `m`, `h`) |
| `allow_failure` | `false` | Continue profile on failure |
| `decision_on_failure` | `fail` | `fail` or `warn` |

Emit `FOUNDRY_NEEDS_HUMAN` from any gate to pause and request human review.

## CLI reference

```
foundry run <profile>              # run a profile
foundry run <profile> --dry-run    # preview gates without running
foundry doctor                     # check config and environment
foundry latest                     # last run result
foundry explain                    # AI explanation of last failure
foundry schedule install <name>    # install cron from foundry.yaml
foundry schedule list              # list installed schedules
foundry schedule remove <name>     # remove a schedule
foundry daemon start               # persistent scheduler daemon
foundry daemon stop
foundry daemon status
```

## Integrations

**explain** — on failure, calls an AI model and writes `explanation.md` into the run directory.

**agent** — runs an arbitrary shell command (e.g. `claude -p "..."`) after failure, with `{run_dir}` interpolated.

**beads** — opens a beads issue on failure or when human review is needed.

## Run index

Every run is persisted to a local SQLite database (`~/.foundry/runs.db`). Query history with `foundry latest` or inspect directly.

## License

MIT
