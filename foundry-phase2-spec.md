# Foundry Phase 2 SPEC — Agent Integration

Date: 2026-06-16
Status: Draft
Depends on: Phase 1 (foundry-lite/ at foundry-spec.md)

---

## Context

Phase 2 extends the Phase 1 MVP (`foundry-lite/`) with agent-facing features:
LLM-powered failure explanation, parallel gate execution, file-watch re-run,
Dagger runner backend, and a documented PAS/Reckoner integration pattern.

All new code lives in `foundry-lite/src/foundry/`. Phase 1 modules
(`runner.py`, `cli.py`, `config.py`, `output.py`, `doctor.py`, `scheduler.py`)
are extended — not replaced.

---

## New Modules

```
foundry-lite/src/foundry/
  explain.py      # LLM failure explanation
  watcher.py      # file-change re-run loop
  dagger_runner.py  # Dagger pipeline runner backend
```

---

## Feature 1 — `foundry explain --latest`

### CLI

```
foundry explain --latest
foundry explain --run 2026-06-15T12-00-00Z
```

Flags:
- `--latest`: use the most recent run (default if neither specified)
- `--run <run-id>`: use a specific run directory
- `--model <model>`: override LLM model (default: claude-haiku-4-5-20251001)

Output to stdout. Exits 0 always (explain never blocks a run).

### Behavior

1. Load `result.json` from the run directory.
2. If decision is `pass`: print "All gates passed. No explanation needed." and exit.
3. For each failed or timed_out gate:
   - Read the gate log (up to 8KB from the end — tail of log).
   - Call the Anthropic API with a prompt asking for a concise (≤5 bullet) explanation of what failed and why.
4. Print per-gate explanation to stdout in this format:

```
## Gate: lint (failed)

- `src/foo.py:42`: unused import `os`
- `src/bar.py:17`: line too long (120 > 88)

## Gate: test (timed_out)

- Test suite exceeded 5-minute timeout
- Last output: `test_heavy_computation ... `
- Likely cause: infinite loop or hanging fixture
```

### explain.py implementation

```python
import anthropic

def explain_run(run_dir: Path, model: str = "claude-haiku-4-5-20251001") -> str:
    result = json.loads((run_dir / "result.json").read_text())
    if result["decision"] == "pass":
        return "All gates passed. No explanation needed."

    client = anthropic.Anthropic()
    parts = []
    for gate in result["gates"]:
        if gate["status"] not in ("failed", "timed_out"):
            continue
        log_path = Path(gate["log"])
        log_tail = _tail(log_path, 8192) if log_path.exists() else "(no log)"
        msg = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": EXPLAIN_PROMPT.format(
                gate_id=gate["id"],
                status=gate["status"],
                command=gate["command"],
                log=log_tail,
            )}],
        )
        parts.append(f"## Gate: {gate['id']} ({gate['status']})\n\n{msg.content[0].text}")
    return "\n\n".join(parts)

EXPLAIN_PROMPT = """Gate '{gate_id}' {status} running: {command}

Log output (last 8KB):
{log}

In ≤5 concise bullet points, explain what failed and why. Be specific about file paths and line numbers if visible. Do not repeat the log verbatim."""
```

Dependency: `anthropic` — add to `pyproject.toml`.
API key: read from `ANTHROPIC_API_KEY` env var (standard SDK behavior).

---

## Feature 2 — Parallel Gate Execution

### Config

```yaml
profiles:
  quick:
    parallel: true   # optional, default: false
    gates:
      - id: lint
        run: make lint
      - id: typecheck
        run: make typecheck
```

When `parallel: true`, all gates in the profile run concurrently using
`concurrent.futures.ThreadPoolExecutor`. Gates still write to individual log
files. `result.json` records each gate's actual start/finished timestamps.

### Behavior changes

- All gates start simultaneously.
- Fail-fast is NOT applied in parallel mode (no way to cancel already-running gates cleanly in v1; they run to completion).
- `result.json` decision is derived the same way after all gates complete.
- Gates that timed out are killed via `subprocess.kill()`.

### Config schema addition

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `parallel` | bool | no | `false` | Run profile gates concurrently |

### runner.py changes

Add `run_profile_parallel(profile, run_dir)` alongside the existing sequential
`run_profile(profile, run_dir)`. Dispatch based on `profile.parallel`.

---

## Feature 3 — `--watch` mode

### CLI

```
foundry run quick --watch
foundry run quick --watch --debounce 3s
```

Flags (added to `foundry run`):
- `--watch`: re-run the profile whenever a tracked file changes
- `--debounce <duration>`: wait for N seconds of quiet before re-running (default: `2s`)

### Behavior

1. Run the profile once immediately.
2. Watch the repo directory for file changes using `watchdog` library.
3. On change (debounced), clear the terminal and re-run.
4. Print between runs:
   ```
   [foundry] watching for changes (Ctrl-C to stop)...
   ```
5. Ctrl-C exits cleanly with exit 0.

### watcher.py

```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class FoundryWatcher:
    def __init__(self, repo: Path, profile: str, debounce: float):
        ...
    def start(self): ...
    def stop(self): ...
```

Dependency: `watchdog` — add to `pyproject.toml`.

Ignore `.foundry/` directory (run artifacts should not trigger re-runs).

---

## Feature 4 — Dagger Runner Backend

### Config

```yaml
profiles:
  ci:
    gates:
      - id: dagger-pipeline
        dagger:
          module: .         # path to dagger module (where dagger.json lives)
          function: build   # dagger function to call
        timeout: 15m
```

### Gate fields (dagger block)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `dagger.module` | string | yes | — | Path to dagger module root |
| `dagger.function` | string | yes | — | Dagger function name to call |
| `dagger.args` | list[string] | no | [] | Additional CLI args passed to dagger call |

### dagger_runner.py

```python
def run_dagger_gate(gate, log_path: Path, timeout: int) -> GateResult:
    cmd = ["dagger", "call", "-m", gate.dagger.module, gate.dagger.function] + gate.dagger.args
    return _run_subprocess(cmd, log_path, timeout)
```

Invocation: `dagger call -m <module> <function> [args...]`

Doctor check: add `[PASS/FAIL] dagger: binary found at <path>` to `foundry doctor`
when any gate uses a `dagger` block.

### Runner type detection addition

| Block | Runner | Invocation |
|-------|--------|------------|
| `dagger` block | dagger | `dagger call -m <module> <function>` |

### config.py schema changes

Add `dagger` as a mutually-exclusive alternative to `run` and `act` in gate
validation. `validate_config` must enforce exactly one of `run`, `act`, or
`dagger`.

---

## Feature 5 — PAS/Reckoner Integration Pattern

### Documentation file

Write `foundry-lite/INTEGRATION.md` describing how PAS pipelines and Reckoner
agents consume Foundry output.

Contents:

#### Reading result.json

```python
import json, subprocess

result = subprocess.run(
    ["foundry", "run", "quick", "--json"],
    capture_output=True, text=True
)
data = json.loads(result.stdout)

if data["agent_recommendation"]["safe_to_continue"]:
    # proceed to next step
else:
    reason = data["agent_recommendation"]["summary"]
    # halt or escalate
```

#### PAS pipeline node pattern

Show a `.dot` pipeline node that calls `foundry run quick --json`, reads
`safe_to_continue`, and branches to either `continue_work` or `halt`.

#### Reckoner gate pattern

Show how a Reckoner step definition wraps `foundry run --json` as a gate.

#### Environment requirements

- `foundry` on PATH
- `ANTHROPIC_API_KEY` set (only for `foundry explain`)
- Repo directory passed via `--repo` flag for non-CWD runs

### No new Python module needed — documentation only.

---

## CLI changes summary

| Command | Change |
|---------|--------|
| `foundry explain` | New subcommand |
| `foundry run <profile> --watch` | New flag |
| `foundry run <profile> --debounce <dur>` | New flag (requires --watch) |
| `foundry doctor` | Add dagger check |

---

## Dependencies to add

```toml
dependencies = [
  "pyyaml>=6.0",
  "croniter>=2.0",
  "anthropic>=0.25",
  "watchdog>=4.0",
]
```

---

## Error handling

| Scenario | Behavior |
|----------|----------|
| `ANTHROPIC_API_KEY` not set | Exit 1 with "ANTHROPIC_API_KEY not set. Required for foundry explain." |
| LLM API error | Print error to stderr, exit 1 |
| `dagger` binary missing | Exit 1 before run; doctor catches this |
| `--watch` with `--json` | Exit 1 with "Cannot use --json with --watch" |
