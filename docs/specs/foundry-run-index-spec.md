---
title: "Foundry — Run Index & Approval Gate SPEC"
status: draft
date: 2026-06-27
depends-on: docs/specs/prd-foundry-run-index.md
---

# SPEC: Foundry — Run Index & Approval Gate

## Files Changed

| File | Change |
|------|--------|
| `foundry-lite/src/foundry/output.py` | Add `write_run_index`, `write_next_action`, `resolve_next_action_for_run` |
| `foundry-lite/src/foundry/runner.py` | Call index writer after every run; auto-explain on fail |
| `foundry-lite/src/foundry/explain.py` | Add `extract_next_action` |
| `foundry-lite/src/foundry/config.py` | Add `approval_required` to `integrations.agent` |

---

## Phase 2 — SQLite Index Writer

**`foundry-lite/src/foundry/output.py`** (modify)

Add `write_run_index(run_dir, result)` called from the same place as `write_result` and `write_summary`.

```python
import json
import sqlite3
from pathlib import Path

FOUNDRY_DB = Path.home() / ".foundry" / "foundry.db"

def _ensure_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id        TEXT PRIMARY KEY,
            repo_path     TEXT NOT NULL,
            profile       TEXT NOT NULL,
            decision      TEXT NOT NULL,
            started_at    TEXT NOT NULL,
            finished_at   TEXT NOT NULL,
            gate_count    INTEGER NOT NULL DEFAULT 0,
            failed_gates  TEXT,        -- JSON array of failed gate ids
            result_path   TEXT NOT NULL,
            summary_path  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS next_actions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id              TEXT NOT NULL REFERENCES runs(run_id),
            repo_path           TEXT NOT NULL,
            profile             TEXT NOT NULL,
            failed_gate_ids     TEXT NOT NULL,   -- JSON array
            explanation         TEXT,
            next_action         TEXT,
            status              TEXT NOT NULL DEFAULT 'pending_approval'
                CHECK (status IN ('pending_approval','approved','dismissed','resolved')),
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            agent_dispatched_at TEXT,
            resolved_at         TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_runs_repo ON runs(repo_path);
        CREATE INDEX IF NOT EXISTS idx_runs_decision ON runs(decision);
        CREATE INDEX IF NOT EXISTS idx_next_actions_status ON next_actions(status);
    """)
    # prune old data
    conn.execute("DELETE FROM runs WHERE finished_at < datetime('now', '-90 days')")
    conn.execute("""
        DELETE FROM next_actions
        WHERE status = 'resolved' AND resolved_at < datetime('now', '-30 days')
    """)

def write_run_index(run_dir: Path, result: dict):
    FOUNDRY_DB.parent.mkdir(parents=True, exist_ok=True)
    failed_gates = [
        g["id"] for g in result.get("gates", [])
        if g["status"] in ("failed", "timed_out")
    ]
    with sqlite3.connect(str(FOUNDRY_DB)) as conn:
        _ensure_schema(conn)
        conn.execute("""
            INSERT OR REPLACE INTO runs
              (run_id, repo_path, profile, decision, started_at, finished_at,
               gate_count, failed_gates, result_path, summary_path)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            result["run_id"],
            result["repo"]["path"],
            result["profile"],
            result["decision"],
            result["started_at"],
            result["finished_at"],
            len(result.get("gates", [])),
            json.dumps(failed_gates),
            str(run_dir / "result.json"),
            str(run_dir / "summary.md"),
        ))

def write_next_action(run_dir: Path, result: dict, explanation: str, next_action: str):
    failed_gates = [
        g["id"] for g in result.get("gates", [])
        if g["status"] in ("failed", "timed_out")
    ]
    with sqlite3.connect(str(FOUNDRY_DB)) as conn:
        _ensure_schema(conn)
        conn.execute("""
            INSERT INTO next_actions
              (run_id, repo_path, profile, failed_gate_ids, explanation, next_action, status)
            VALUES (?,?,?,?,?,?,'pending_approval')
        """, (
            result["run_id"],
            result["repo"]["path"],
            result["profile"],
            json.dumps(failed_gates),
            explanation,
            next_action,
        ))

def resolve_next_action_for_run(repo_path: str, profile: str):
    """Mark approved next_actions resolved when a re-run passes."""
    with sqlite3.connect(str(FOUNDRY_DB)) as conn:
        conn.execute("""
            UPDATE next_actions
            SET status = 'resolved', resolved_at = datetime('now')
            WHERE status = 'approved'
              AND repo_path = ?
              AND profile = ?
        """, (repo_path, profile))
```

---

## Phase 3 — Auto-Explain on Failure

**`foundry-lite/src/foundry/runner.py`** (modify)

After `write_run_index`, if `decision == "fail"`:

```python
from foundry.explain import explain_run, extract_next_action
from foundry.output import write_next_action

if result["decision"] == "fail":
    try:
        explain_cfg = (integrations_cfg or {}).get("explain", {})
        model = explain_cfg.get("model", "claude-haiku-4-5-20251001")
        explanation = explain_run(run_dir, model)
        next_action = extract_next_action(explanation)
        write_next_action(run_dir, result, explanation, next_action)
    except Exception as e:
        print(f"[foundry] auto-explain failed: {e}", file=sys.stderr)
        # never block the run on explain failure
```

Also in `runner.py`, if `decision == "pass"`, resolve any approved next_actions:

```python
from foundry.output import resolve_next_action_for_run

if result["decision"] == "pass":
    resolve_next_action_for_run(result["repo"]["path"], result["profile"])
```

**`foundry-lite/src/foundry/explain.py`** (modify)

Add `extract_next_action`:

```python
import anthropic

def extract_next_action(explanation: str) -> str:
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content":
            f"Given this gate failure explanation:\n\n{explanation}\n\n"
            "Write ONE sentence describing the most important action to fix this. "
            "Start with a verb. No preamble."
        }],
    )
    return msg.content[0].text.strip()
```

---

## Phase 5 (Foundry side) — Approval Gate Config

**`foundry-lite/src/foundry/config.py`** (modify)

Add `approval_required` to `integrations.agent` validation:

```python
# integrations.agent.approval_required: bool (default False)
# When True:  on failure, write next_action with status=pending_approval
#             and do NOT fire integrations.agent.command automatically.
# When False: existing auto-fire behavior unchanged.
```

In `runner.py`, check flag before firing agent:

```python
agent_cfg = config.get("integrations", {}).get("agent", {})
if agent_cfg.get("on_failure") and result["decision"] == "fail":
    if agent_cfg.get("approval_required"):
        pass  # next_action already written above; exit without firing
    else:
        fire_agent(agent_cfg["command"], run_dir)
```

---

## foundry.yaml Schema Addition

```yaml
integrations:
  agent:
    on_failure: true
    approval_required: true    # NEW: write to next_actions, do not auto-fire
    command: 'claude -p "Fix the failing gate. Context in {run_dir}/result.json and {run_dir}/explanation.md" --dangerously-skip-permissions'
```

---

## Testing

**Phase 2:** Run `foundry run quick` on any repo. Verify `~/.foundry/foundry.db` exists and `runs` table has a new row with correct `decision`, `repo_path`, `profile`.

**Phase 3:** Run with a gate that fails. Verify `next_actions` has a row with `explanation` and `next_action` populated in the same process. Verify that missing `ANTHROPIC_API_KEY` logs a warning and does not fail the run.

**Phase 5:** Set `approval_required: true` in a repo's `foundry.yaml`. Trigger a failure. Verify no agent fires and `next_actions` row has `status: pending_approval`. Simulate an approved re-run that passes; verify row becomes `status: resolved`.
