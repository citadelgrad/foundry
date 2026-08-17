import json
import sqlite3
from pathlib import Path

FOUNDRY_DB = Path.home() / ".foundry" / "foundry.db"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id        TEXT PRIMARY KEY,
            repo_path     TEXT NOT NULL,
            profile       TEXT NOT NULL,
            decision      TEXT NOT NULL,
            started_at    TEXT NOT NULL,
            finished_at   TEXT NOT NULL,
            gate_count    INTEGER NOT NULL DEFAULT 0,
            failed_gates  TEXT,
            result_path   TEXT NOT NULL,
            summary_path  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS next_actions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id              TEXT NOT NULL REFERENCES runs(run_id),
            repo_path           TEXT NOT NULL,
            profile             TEXT NOT NULL,
            failed_gate_ids     TEXT NOT NULL,
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
    conn.execute("DELETE FROM runs WHERE finished_at < datetime('now', '-90 days')")
    conn.execute("DELETE FROM next_actions WHERE status = 'resolved' AND resolved_at < datetime('now', '-30 days')")


def write_run_index(run_dir: Path, result: dict) -> None:
    FOUNDRY_DB.parent.mkdir(parents=True, exist_ok=True)
    failed_gates = [g["id"] for g in result.get("gates", []) if g["status"] in ("failed", "timed_out")]
    with sqlite3.connect(str(FOUNDRY_DB)) as conn:
        _ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO runs"
            " (run_id, repo_path, profile, decision, started_at, finished_at, gate_count, failed_gates, result_path, summary_path)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
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
            ),
        )


def write_next_action(run_dir: Path, result: dict, explanation: str, next_action: str) -> None:
    failed_gates = [g["id"] for g in result.get("gates", []) if g["status"] in ("failed", "timed_out")]
    with sqlite3.connect(str(FOUNDRY_DB)) as conn:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO next_actions"
            " (run_id, repo_path, profile, failed_gate_ids, explanation, next_action, status)"
            " VALUES (?, ?, ?, ?, ?, ?, 'pending_approval')",
            (
                result["run_id"],
                result["repo"]["path"],
                result["profile"],
                json.dumps(failed_gates),
                explanation,
                next_action,
            ),
        )


def resolve_next_action_for_run(repo_path: str, profile: str) -> None:
    with sqlite3.connect(str(FOUNDRY_DB)) as conn:
        _ensure_schema(conn)
        conn.execute(
            "UPDATE next_actions SET status = 'resolved', resolved_at = datetime('now')"
            " WHERE status IN ('approved', 'pending_approval') AND repo_path = ? AND profile = ?",
            (repo_path, profile),
        )
