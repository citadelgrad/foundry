# Foundry Phase 3 SPEC — Ecosystem

Date: 2026-06-16
Status: Draft
Depends on: Phase 1 (foundry-spec.md), Phase 2 (foundry-phase2-spec.md)

---

## Context

Phase 3 closes the loop between Foundry and the broader local tooling ecosystem:
Beads issue tracking, LLM gateway policy evidence, `foundry init` bootstrapping,
and multi-repo support via a global config.

All code extends `foundry-lite/src/foundry/`. No new top-level modules except
where specified below.

---

## New Modules

```
foundry-lite/src/foundry/
  global_config.py   # read/write ~/.config/foundry/config.yaml
  init_cmd.py        # foundry init generator
```

---

## Feature 1 — Beads Issue Creation on Failure

### Behavior

When `foundry run` produces `decision: fail` or `decision: needs_human`,
Foundry optionally creates a Beads issue capturing the failure.

### Config

```yaml
version: 1

integrations:
  beads:
    on_failure: true       # create issue when decision=fail
    on_needs_human: true   # create issue when decision=needs_human
    labels:
      - foundry
      - automated
```

`integrations.beads` is optional. Default: no Beads integration.

### Behavior

After a run with decision `fail` or `needs_human` (and integration enabled):

1. Call `bd create` via subprocess with:
   - `--title`: `"foundry: <profile> <decision> @ <commit-short>"`
   - `--description`: the `summary.md` content (truncated to 4KB)
   - `--type`: `bug`
   - `--priority`: `2`

2. Print to stderr:
   ```
   [foundry] beads issue created: beads-NNN
   ```

3. Add `beads_issue_id` to `result.json` at the top level:
   ```json
   { ..., "beads_issue_id": "beads-NNN" }
   ```

### Implementation in runner.py

```python
def maybe_create_beads_issue(config, result: dict, run_dir: Path):
    beads_cfg = config.get("integrations", {}).get("beads", {})
    if not beads_cfg:
        return
    decision = result["decision"]
    if decision == "fail" and not beads_cfg.get("on_failure"):
        return
    if decision == "needs_human" and not beads_cfg.get("on_needs_human"):
        return

    summary = (run_dir / "summary.md").read_text()[:4096]
    profile = result["profile"]
    commit = result["repo"]["commit"][:7]
    title = f"foundry: {profile} {decision} @ {commit}"

    proc = subprocess.run(
        ["bd", "create", "--title", title, "--description", summary,
         "--type", "bug", "--priority", "2"],
        capture_output=True, text=True
    )
    if proc.returncode == 0:
        issue_id = proc.stdout.strip().split()[-1]  # bd create prints "Created beads-NNN"
        print(f"[foundry] beads issue created: {issue_id}", file=sys.stderr)
        result["beads_issue_id"] = issue_id
```

### Error handling

| Scenario | Behavior |
|----------|----------|
| `bd` not on PATH | Warn to stderr, skip (do not fail the run) |
| `bd create` exits non-zero | Warn to stderr, skip |

Beads failures never fail a Foundry run.

---

## Feature 2 — LLM Gateway Policy Evidence Capture

### Purpose

When Foundry runs gates on behalf of an agent pipeline, it captures a signed
evidence record that LLM gateway policies (e.g., PAS trust anchors) can verify
to confirm the gate ran and what decision it produced.

### Evidence file

After each run, write `.foundry/runs/<run-id>/evidence.json`:

```json
{
  "schema_version": 1,
  "run_id": "2026-06-15T12-00-00Z",
  "decision": "pass",
  "profile": "quick",
  "repo_commit": "abc123def456",
  "result_hash": "sha256:<hex>",
  "foundry_version": "0.1.0",
  "issued_at": "2026-06-15T12:03:14Z"
}
```

`result_hash` is the SHA-256 of the canonical JSON bytes of `result.json`
(sorted keys, no trailing newline).

### Implementation in output.py

```python
import hashlib

def write_evidence(run_dir: Path, result: dict):
    result_bytes = json.dumps(result, sort_keys=True).encode()
    result_hash = "sha256:" + hashlib.sha256(result_bytes).hexdigest()
    evidence = {
        "schema_version": 1,
        "run_id": result["run_id"],
        "decision": result["decision"],
        "profile": result["profile"],
        "repo_commit": result["repo"]["commit"],
        "result_hash": result_hash,
        "foundry_version": result.get("foundry_version", "unknown"),
        "issued_at": result["finished_at"],
    }
    (run_dir / "evidence.json").write_text(json.dumps(evidence, indent=2))
```

Call `write_evidence` in `output.py` alongside `write_result` and `write_summary`.

### No signing in v1

The evidence record is not cryptographically signed in Phase 3. The `result_hash`
allows a consumer to verify `result.json` has not been tampered with post-run.
A proper signing key infrastructure is out of scope.

### `foundry latest --evidence`

```
foundry latest --evidence
```

Prints `evidence.json` from the latest run to stdout.

---

## Feature 3 — `foundry init`

### CLI

```
foundry init
foundry init --profile quick
foundry init --profile full
```

Generates a `foundry.yaml` in the current directory based on detected repo
characteristics. Exits 1 if `foundry.yaml` already exists (with message:
"foundry.yaml already exists. Remove it first or edit manually.").

### Detection logic (in order)

| Detection | Generated gate |
|-----------|----------------|
| `Makefile` with `lint` target | `run: make lint` |
| `Makefile` with `test` target | `run: make test` |
| `Makefile` with `typecheck` target | `run: make typecheck` |
| `justfile` with `lint` recipe | `run: just lint` |
| `justfile` with `test` recipe | `run: just test` |
| `.github/workflows/*.yml` exists | `act` gate for first workflow |
| No runners detected | Commented placeholder |

### Generated output (example)

```yaml
version: 1

profiles:
  quick:
    gates:
      - id: lint
        run: make lint
      - id: test
        run: make test

  full:
    gates:
      - id: lint
        run: make lint
      - id: test
        run: make test
      - id: ci
        act:
          workflow: .github/workflows/ci.yml
          event: pull_request
        timeout: 20m

schedules:
  nightly:
    profile: full
    cron: "0 2 * * *"
```

### init_cmd.py

```python
def detect_gates(repo: Path) -> list[dict]:
    gates = []
    makefile = repo / "Makefile"
    if makefile.exists():
        content = makefile.read_text()
        for target in ("lint", "test", "typecheck", "build"):
            if f"{target}:" in content:
                gates.append({"id": target, "run": f"make {target}"})
    # justfile, .github/workflows detection follows same pattern
    return gates

def generate_foundry_yaml(repo: Path) -> str:
    gates = detect_gates(repo)
    # render yaml from gates list
    ...
```

### Stdout output

```
[foundry] detected: Makefile (lint, test)
[foundry] detected: .github/workflows/ci.yml
[foundry] wrote foundry.yaml (3 gates in quick, 4 gates in full)
```

---

## Feature 4 — Multi-Repo Support

### Global config file

`~/.config/foundry/config.yaml`:

```yaml
version: 1
repos:
  - path: /repos/myapp
    alias: myapp
  - path: /repos/other-project
    alias: other
```

### CLI additions

```
foundry repos add [--alias <name>]    # register CWD (or --repo path) in global config
foundry repos list                    # list all registered repos with last run status
foundry repos remove <alias-or-path>  # remove entry
```

#### `foundry repos list` output

```
myapp        /repos/myapp              2026-06-15  pass
other        /repos/other-project      (no runs)   —
```

### `--repo` flag persistence

When `foundry run` is called without `--repo`, it checks `~/.config/foundry/config.yaml`
for a registered repo whose `path` matches the current working directory.
If found, the alias is shown in progress output:
```
[foundry] profile: quick (repo: myapp)
```

### global_config.py

```python
CONFIG_PATH = Path.home() / ".config" / "foundry" / "config.yaml"

def load_global_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"version": 1, "repos": []}
    return yaml.safe_load(CONFIG_PATH.read_text())

def save_global_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(yaml.dump(cfg, default_flow_style=False))

def add_repo(path: Path, alias: str | None = None):
    cfg = load_global_config()
    alias = alias or path.name
    cfg["repos"] = [r for r in cfg["repos"] if r["path"] != str(path)]
    cfg["repos"].append({"path": str(path), "alias": alias})
    save_global_config(cfg)
```

---

## CLI changes summary

| Command | Change |
|---------|--------|
| `foundry init` | New subcommand |
| `foundry repos add/list/remove` | New subcommand group |
| `foundry latest --evidence` | New flag |
| `foundry run` | Beads issue creation post-run (if configured) |

---

## Run directory layout addition

```
.foundry/runs/<run-id>/
  result.json
  summary.md
  metadata.json
  evidence.json     # NEW in Phase 3
  logs/
    <gate-id>.log
```

---

## Error handling

| Scenario | Behavior |
|----------|----------|
| `~/.config/foundry/` not writable | Exit 1 with path and permission error |
| `foundry init` with existing `foundry.yaml` | Exit 1 with message |
| `bd` missing for Beads integration | Warn, skip — never fail the run |
| No runners detected by `foundry init` | Generate placeholder yaml with commented examples |
