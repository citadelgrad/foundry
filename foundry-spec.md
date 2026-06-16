# Foundry SPEC — Technical Specification

Date: 2026-06-16  
Status: Draft

---

## Implementation Language

**Python 3.11+, managed with `uv`.**

Rationale:
- Fastest path to a working prototype
- subprocess, JSON, YAML, cron/launchd all have stdlib or near-stdlib coverage
- Easy to ship as a single binary via PyInstaller / Nuitka if needed later
- Graduate to Rust only if startup latency or packaging becomes a real problem

```
foundry-lite/         # initial prototype directory
  pyproject.toml
  src/
    foundry/
      __main__.py     # entrypoint: `python -m foundry` or `uv run foundry`
      cli.py          # argument parsing (stdlib argparse)
      config.py       # foundry.yaml loading and validation
      runner.py       # gate execution engine
      scheduler.py    # cron/launchd install/remove
      output.py       # result.json + summary.md writer
      doctor.py       # environment checks
```

---

## `foundry.yaml` Schema (canonical)

**Gate-centric model** (chosen over stage-centric — self-contained, no indirection layer).

```yaml
version: 1

profiles:
  quick:
    gates:
      - id: lint
        run: make lint
        timeout: 5m           # optional, default: 10m
      - id: test
        run: make test

  gha:
    gates:
      - id: github-actions
        act:
          workflow: .github/workflows/ci.yml
          event: pull_request
        timeout: 20m

  full:
    gates:
      - id: actionlint
        run: actionlint
      - id: local-ci
        act:
          workflow: .github/workflows/ci.yml
          event: pull_request
      - id: mutation
        run: make mutation
        allow_failure: true
        decision_on_failure: warn
        timeout: 30m

schedules:
  nightly:
    profile: full
    cron: "0 2 * * *"
```

### Gate fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `id` | string | yes | — | Unique gate identifier within profile |
| `run` | string | no* | — | Shell command to execute |
| `act` | object | no* | — | Act runner config (mutually exclusive with `run`) |
| `act.workflow` | string | yes if act | — | Relative path to workflow file |
| `act.event` | string | no | `push` | GitHub event to simulate |
| `timeout` | string | no | `10m` | Duration string: `30s`, `5m`, `1h` |
| `allow_failure` | bool | no | `false` | Gate failure does not cause overall `fail` decision |
| `decision_on_failure` | string | no | `fail` | Override decision when this gate fails: `warn` only valid with `allow_failure: true` |

*Exactly one of `run` or `act` must be present.

### Runner type detection

Runner type is inferred from the `run` string prefix:

| Prefix | Runner | Invocation |
|--------|--------|------------|
| `make ` | make | `make <target>` |
| `just ` | just | `just <recipe>` |
| `task ` | task | `task <task>` |
| `mise run ` | mise | `mise run <task>` |
| anything else | shell | `sh -c "<command>"` |
| (act block) | act | `act <event> -W <workflow>` |

---

## `result.json` Schema (v1, canonical)

```json
{
  "schema_version": 1,
  "run_id": "2026-06-15T12-00-00Z",
  "profile": "quick",
  "decision": "pass",
  "exit_code": 0,
  "started_at": "2026-06-15T12:00:00Z",
  "finished_at": "2026-06-15T12:03:14Z",
  "repo": {
    "path": "/repos/myapp",
    "commit": "abc123def456",
    "branch": "main",
    "dirty": false
  },
  "gates": [
    {
      "id": "lint",
      "runner": "make",
      "command": "make lint",
      "status": "passed",
      "exit_code": 0,
      "started_at": "2026-06-15T12:00:00Z",
      "finished_at": "2026-06-15T12:00:42Z",
      "duration_ms": 42000,
      "log": "logs/lint.log",
      "log_truncated": false,
      "allow_failure": false
    }
  ],
  "agent_recommendation": {
    "safe_to_continue": true,
    "safe_to_commit": true,
    "needs_human": false,
    "summary": "All quick gates passed."
  }
}
```

### Decision derivation rules (evaluated in order)

1. Any gate has `status: needs_human` → decision = `needs_human`
2. Any required gate (`allow_failure: false`) has `status: failed` or `timed_out` → decision = `fail`
3. Any `allow_failure: true` gate failed with `decision_on_failure: warn` → decision = `warn`
4. All required gates passed → decision = `pass`

`safe_to_continue`: true if decision is `pass` or `warn`.  
`safe_to_commit`: true if decision is `pass` (not `warn`).  
`needs_human`: true if decision is `needs_human`.

### Gate status values

- `passed` — exit code 0
- `failed` — exit code non-zero
- `timed_out` — exceeded gate timeout
- `skipped` — gate was not run (e.g. profile excluded it; reserved for future conditional gates)
- `needs_human` — gate emitted a sentinel string to stdout (see below)

### `needs_human` sentinel

A gate sets status `needs_human` if its stdout or stderr contains the literal string:

```
FOUNDRY_NEEDS_HUMAN
```

This lets custom scripts signal that automated continuation is unsafe.

---

## Run Directory Layout

```
.foundry/
  runs/
    2026-06-15T12-00-00Z/
      result.json
      summary.md
      metadata.json
      logs/
        lint.log
        test.log
    2026-06-15T02-00-00Z/      # scheduled run, same format
      result.json
      ...
  latest -> runs/2026-06-15T12-00-00Z   # symlink, updated after each run
```

`metadata.json`:
```json
{
  "foundry_version": "0.1.0",
  "python_version": "3.11.4",
  "platform": "darwin",
  "log_size_cap_bytes": 524288
}
```

---

## CLI Specification

### `foundry doctor`

```
foundry doctor
```

Prints per-check result to stdout. Format:
```
[PASS] git: repo detected at /repos/myapp
[PASS] docker: daemon running
[PASS] act: found at /usr/local/bin/act (v0.2.60)
[PASS] foundry.yaml: valid
[PASS] runners: Makefile detected
[WARN] .github/workflows: no workflows found (Act gates will fail)
```

Exit 0 if all `[PASS]` and `[WARN]` only. Exit 1 if any `[FAIL]`.

Checks marked `[WARN]` do not block exit but indicate potential issues.

Required vs warning checks:

| Check | Failure mode |
|-------|-------------|
| git repo | FAIL |
| foundry.yaml present | FAIL |
| foundry.yaml parseable | FAIL |
| Docker/Colima (only if Act gates configured) | FAIL |
| Act binary (only if Act gates configured) | FAIL |
| Makefile/justfile/etc (only if shell/make gates configured) | WARN |
| .github/workflows (only if Act gates configured) | WARN |

### `foundry run <profile>`

```
foundry run quick
foundry run full --json
foundry run gha --json
```

`<profile>` is a positional argument matching a key in `foundry.yaml.profiles`.

Flags:
- `--json`: write `result.json` to stdout after run completes (in addition to file)
- `--dry-run`: print gates that would run without executing them
- `--profile <name>`: alias for positional profile (for scripting convenience)

Progress output to stderr during run:
```
[foundry] profile: quick
[foundry] gate lint → running (make lint)
[foundry] gate lint → passed (42s)
[foundry] gate test → running (make test)
[foundry] gate test → passed (91s)
[foundry] decision: pass
[foundry] evidence: .foundry/runs/2026-06-15T12-00-00Z/
```

`stdout` is clean (only `--json` output if requested). Agents parse stdout; humans read stderr.

### `foundry latest`

```
foundry latest --summary     # print summary.md to stdout
foundry latest --json        # print result.json to stdout
foundry latest               # print one-line status
```

One-line format (no flags):
```
2026-06-15T12:00:00Z  quick  pass  135s
```

### `foundry schedule install <name>`

```
foundry schedule install nightly
```

Reads `schedules.nightly` from `foundry.yaml`. Installs OS scheduler entry.

macOS: writes to `~/Library/LaunchAgents/com.foundry.<name>.plist`  
Linux: appends to user crontab via `crontab -l | crontab -`

The installed command:
```bash
/path/to/foundry run <profile> --repo /path/to/repo
```

`--repo` is the absolute path of the directory where `foundry schedule install` was run. This makes scheduled runs repo-scoped even when invoked from cron's default `$HOME`.

### `foundry schedule list`

```
foundry schedule list
```

Output:
```
nightly   full   0 2 * * *   /repos/myapp
```

### `foundry schedule remove <name>`

```
foundry schedule remove nightly
```

Removes the OS scheduler entry.

---

## Gate Execution Engine

```python
# ponytail: sequential only in v1, parallel would need output interleaving logic
for gate in profile.gates:
    result = run_gate(gate)
    if result.status == "failed" and not gate.allow_failure:
        break  # stop on first required failure
```

**Fail-fast**: stop executing remaining gates when a required gate fails. This mirrors CI behavior and avoids running expensive gates after a critical failure. Gates after the failure point get `status: skipped`.

**Timeout**: implemented via `subprocess` with `timeout=` parameter. On timeout, gate gets `status: timed_out`, which is treated as `failed` for decision purposes.

**Log capture**: stdout and stderr are merged and written to the log file. A tee is used so terminal output is not suppressed during interactive runs.

**Log size cap**: log files are capped at 512KB. If exceeded, the file is truncated and `log_truncated: true` is set in the gate result.

---

## `summary.md` Template

```markdown
# Foundry Run: quick

**Decision:** pass  
**Profile:** quick  
**Commit:** abc123def456 (main)  
**Duration:** 2m 15s  
**Run ID:** 2026-06-15T12-00-00Z

## Gates

| Gate | Status | Duration |
|------|--------|----------|
| lint | ✅ passed | 42s |
| test | ✅ passed | 91s |

## Agent Recommendation

Safe to continue: **yes**  
Safe to commit: **yes**  
Needs human: no

> All quick gates passed.
```

---

## Scheduling Implementation

### macOS (launchd)

Write `~/Library/LaunchAgents/com.foundry.<name>.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "...">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.foundry.nightly</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/foundry</string>
    <string>run</string>
    <string>full</string>
    <string>--repo</string>
    <string>/repos/myapp</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>2</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/tmp/foundry-nightly.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/foundry-nightly.log</string>
</dict>
</plist>
```

Then `launchctl load ~/Library/LaunchAgents/com.foundry.<name>.plist`.

### Linux (cron)

Parse the cron expression from `foundry.yaml` and append:
```
0 2 * * *  /path/to/foundry run full --repo /repos/myapp >> /tmp/foundry-nightly.log 2>&1
```

Cron parsing: use `croniter` library for validation only. Do not reimplement cron.

---

## Config Loading and Validation

`foundry.yaml` is loaded with `tomllib` if `.toml` or `PyYAML` if `.yaml`. MVP: YAML only.

Validation (no third-party schema library needed in v1):

```python
def validate_config(cfg: dict) -> list[str]:
    errors = []
    if cfg.get("version") != 1:
        errors.append("version must be 1")
    for name, profile in cfg.get("profiles", {}).items():
        for gate in profile.get("gates", []):
            has_run = "run" in gate
            has_act = "act" in gate
            if has_run == has_act:  # both or neither
                errors.append(f"gate {gate.get('id')} must have exactly one of 'run' or 'act'")
    return errors
```

Return errors to user; do not run if any errors.

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `foundry.yaml` missing | Exit 1 with "foundry.yaml not found. Run foundry doctor." |
| `foundry.yaml` invalid YAML | Exit 1 with parse error and line number |
| Profile not found | Exit 1 with available profile names |
| Docker not running (Act gate) | Gate fails with `status: failed`, reason in log |
| Act binary missing (Act gate) | Exit 1 before run starts; doctor would have caught this |
| Gate timeout | `status: timed_out`, treated as failure |
| `.foundry/` not writable | Exit 1 before run starts |
| Concurrent runs | No lock in v1; last writer wins for `latest` symlink |

---

## `.gitignore` Recommendation

Foundry generates this in `.foundry/` on first run if not present:

```gitignore
# Foundry run artifacts — add to your repo's .gitignore
.foundry/runs/
```

`foundry.yaml` should be committed.

---

## Phases

### Phase 1 — MVP (this SPEC)

- `foundry doctor`
- `foundry run <profile>` with shell/make/just/task/mise/act gates
- `result.json`, `summary.md`, `metadata.json`
- `foundry latest`
- `foundry schedule install/list/remove`
- Decision model: `pass / warn / fail / needs_human`

### Phase 2 — Agent integration

- `foundry explain --latest` (LLM call to summarize failures)
- Parallel gate execution option
- `--watch` mode: re-run on file change
- Dagger runner backend
- PAS/Reckoner documented integration pattern

### Phase 3 — Ecosystem

- Beads issue creation on failure
- LLM gateway policy evidence capture
- `foundry init` / `foundry.yaml` generator
- Multi-repo support (`--repo` flag persisted in a global config)
