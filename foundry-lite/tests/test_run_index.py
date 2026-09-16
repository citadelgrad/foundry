"""Tests for the SQLite run index, auto-explain, and approval gate logic."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result(decision="pass", run_id="run-1", gates=None, profile="quick", repo="/repo"):
    return {
        "run_id": run_id,
        "profile": profile,
        "decision": decision,
        "started_at": "2026-06-27T00:00:00Z",
        "finished_at": "2026-06-27T00:01:00Z",
        "repo": {"path": repo},
        "gates": gates or [],
    }


def _gate(id_, status):
    return {"id": id_, "status": status}


@pytest.fixture(autouse=False)
def db(tmp_path, monkeypatch):
    """Redirect FOUNDRY_DB to a temp file; return the Path."""
    import foundry.run_index as mod
    db_path = tmp_path / "foundry.db"
    monkeypatch.setattr(mod, "FOUNDRY_DB", db_path)
    return db_path


def _query(db_path, sql, *params):
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# write_run_index
# ---------------------------------------------------------------------------

class TestWriteRunIndex:
    def test_row_appears(self, db, tmp_path):
        from foundry.run_index import write_run_index
        write_run_index(tmp_path, _result())
        rows = _query(db, "SELECT run_id FROM runs")
        assert rows == [("run-1",)]

    def test_decision_stored(self, db, tmp_path):
        from foundry.run_index import write_run_index
        write_run_index(tmp_path, _result(decision="fail"))
        row = _query(db, "SELECT decision FROM runs WHERE run_id='run-1'")[0]
        assert row[0] == "fail"

    def test_gate_count(self, db, tmp_path):
        from foundry.run_index import write_run_index
        gates = [_gate("lint", "passed"), _gate("test", "failed"), _gate("type", "passed")]
        write_run_index(tmp_path, _result(gates=gates))
        row = _query(db, "SELECT gate_count FROM runs WHERE run_id='run-1'")[0]
        assert row[0] == 3

    def test_failed_gates_only_failed_and_timed_out(self, db, tmp_path):
        from foundry.run_index import write_run_index
        gates = [
            _gate("lint", "failed"),
            _gate("test", "passed"),
            _gate("type", "timed_out"),
            _gate("sec", "skipped"),
        ]
        write_run_index(tmp_path, _result(gates=gates))
        raw = _query(db, "SELECT failed_gates FROM runs WHERE run_id='run-1'")[0][0]
        assert set(json.loads(raw)) == {"lint", "type"}

    def test_passed_gate_not_in_failed_list(self, db, tmp_path):
        from foundry.run_index import write_run_index
        gates = [_gate("lint", "passed")]
        write_run_index(tmp_path, _result(gates=gates))
        raw = _query(db, "SELECT failed_gates FROM runs WHERE run_id='run-1'")[0][0]
        assert json.loads(raw) == []

    def test_needs_human_gate_not_in_failed_list(self, db, tmp_path):
        from foundry.run_index import write_run_index
        gates = [_gate("review", "needs_human")]
        write_run_index(tmp_path, _result(gates=gates))
        raw = _query(db, "SELECT failed_gates FROM runs WHERE run_id='run-1'")[0][0]
        assert json.loads(raw) == []

    def test_upsert_overwrites_on_duplicate_run_id(self, db, tmp_path):
        from foundry.run_index import write_run_index
        write_run_index(tmp_path, _result(decision="fail"))
        write_run_index(tmp_path, _result(decision="pass"))
        rows = _query(db, "SELECT decision FROM runs WHERE run_id='run-1'")
        assert len(rows) == 1
        assert rows[0][0] == "pass"

    def test_result_path_stored(self, db, tmp_path):
        from foundry.run_index import write_run_index
        write_run_index(tmp_path, _result())
        row = _query(db, "SELECT result_path FROM runs WHERE run_id='run-1'")[0]
        assert row[0] == str(tmp_path / "result.json")

    def test_creates_parent_dir(self, tmp_path, monkeypatch):
        import foundry.run_index as mod
        db_path = tmp_path / "subdir" / "foundry.db"
        monkeypatch.setattr(mod, "FOUNDRY_DB", db_path)
        from foundry.run_index import write_run_index
        write_run_index(tmp_path, _result())  # must not raise
        assert db_path.exists()

    def test_timed_out_gate_in_failed_gates_column(self, db, tmp_path):
        from foundry.run_index import write_run_index
        gates = [_gate("ci", "timed_out"), _gate("lint", "passed")]
        write_run_index(tmp_path, _result(gates=gates))
        raw = _query(db, "SELECT failed_gates FROM runs WHERE run_id='run-1'")[0][0]
        assert json.loads(raw) == ["ci"]

    def test_needs_human_gate_excluded_from_failed_gates_column(self, db, tmp_path):
        from foundry.run_index import write_run_index
        gates = [_gate("review", "needs_human"), _gate("lint", "failed")]
        write_run_index(tmp_path, _result(gates=gates))
        raw = _query(db, "SELECT failed_gates FROM runs WHERE run_id='run-1'")[0][0]
        assert json.loads(raw) == ["lint"]


# ---------------------------------------------------------------------------
# write_next_action
# ---------------------------------------------------------------------------

class TestWriteNextAction:
    def test_status_is_pending_approval(self, db, tmp_path):
        from foundry.run_index import write_run_index, write_next_action
        r = _result(decision="fail")
        write_run_index(tmp_path, r)
        write_next_action(tmp_path, r, "lint broke", "Fix the lint errors")
        row = _query(db, "SELECT status FROM next_actions WHERE run_id='run-1'")[0]
        assert row[0] == "pending_approval"

    def test_explanation_and_next_action_stored(self, db, tmp_path):
        from foundry.run_index import write_run_index, write_next_action
        r = _result(decision="fail")
        write_run_index(tmp_path, r)
        write_next_action(tmp_path, r, "Type errors in foo.py", "Add missing type annotations")
        row = _query(db, "SELECT explanation, next_action FROM next_actions WHERE run_id='run-1'")[0]
        assert row[0] == "Type errors in foo.py"
        assert row[1] == "Add missing type annotations"

    def test_failed_gate_ids_includes_timed_out(self, db, tmp_path):
        from foundry.run_index import write_run_index, write_next_action
        gates = [_gate("test", "timed_out")]
        r = _result(decision="fail", gates=gates)
        write_run_index(tmp_path, r)
        write_next_action(tmp_path, r, "explanation", "Fix it")
        raw = _query(db, "SELECT failed_gate_ids FROM next_actions WHERE run_id='run-1'")[0][0]
        assert json.loads(raw) == ["test"]

    def test_failed_gate_ids_excludes_passed(self, db, tmp_path):
        from foundry.run_index import write_run_index, write_next_action
        gates = [_gate("lint", "failed"), _gate("test", "passed")]
        r = _result(decision="fail", gates=gates)
        write_run_index(tmp_path, r)
        write_next_action(tmp_path, r, "x", "y")
        raw = _query(db, "SELECT failed_gate_ids FROM next_actions WHERE run_id='run-1'")[0][0]
        assert json.loads(raw) == ["lint"]

    def test_status_not_approved_by_default(self, db, tmp_path):
        from foundry.run_index import write_run_index, write_next_action
        r = _result(decision="fail")
        write_run_index(tmp_path, r)
        write_next_action(tmp_path, r, "x", "y")
        row = _query(db, "SELECT status FROM next_actions WHERE run_id='run-1'")[0]
        assert row[0] != "approved"


# ---------------------------------------------------------------------------
# resolve_next_action_for_run
# ---------------------------------------------------------------------------

class TestResolveNextActionForRun:
    def _seed(self, db_path, run_id, status, repo="/repo", profile="quick"):
        from foundry.run_index import _ensure_schema
        with sqlite3.connect(str(db_path)) as conn:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO next_actions (run_id, repo_path, profile, failed_gate_ids, status)"
                " VALUES (?, ?, ?, '[]', ?)",
                (run_id, repo, profile, status),
            )

    def test_approved_becomes_resolved(self, db, tmp_path):
        from foundry.run_index import resolve_next_action_for_run
        self._seed(db, "r1", "approved")
        resolve_next_action_for_run("/repo", "quick")
        row = _query(db, "SELECT status FROM next_actions WHERE run_id='r1'")[0]
        assert row[0] == "resolved"

    def test_resolved_at_is_set(self, db, tmp_path):
        from foundry.run_index import resolve_next_action_for_run
        self._seed(db, "r1", "approved")
        resolve_next_action_for_run("/repo", "quick")
        row = _query(db, "SELECT resolved_at FROM next_actions WHERE run_id='r1'")[0]
        assert row[0] is not None

    def test_pending_approval_resolved_on_pass(self, db, tmp_path):
        from foundry.run_index import resolve_next_action_for_run
        self._seed(db, "r2", "pending_approval")
        resolve_next_action_for_run("/repo", "quick")
        row = _query(db, "SELECT status FROM next_actions WHERE run_id='r2'")[0]
        assert row[0] == "resolved"

    def test_dismissed_not_resolved(self, db, tmp_path):
        from foundry.run_index import resolve_next_action_for_run
        self._seed(db, "r3", "dismissed")
        resolve_next_action_for_run("/repo", "quick")
        row = _query(db, "SELECT status FROM next_actions WHERE run_id='r3'")[0]
        assert row[0] == "dismissed"

    def test_different_profile_not_resolved(self, db, tmp_path):
        from foundry.run_index import resolve_next_action_for_run
        self._seed(db, "r4", "approved", profile="quick")
        self._seed(db, "r5", "approved", profile="full")
        resolve_next_action_for_run("/repo", "quick")
        r4 = _query(db, "SELECT status FROM next_actions WHERE run_id='r4'")[0][0]
        r5 = _query(db, "SELECT status FROM next_actions WHERE run_id='r5'")[0][0]
        assert r4 == "resolved"
        assert r5 == "approved"

    def test_different_repo_not_resolved(self, db, tmp_path):
        from foundry.run_index import resolve_next_action_for_run
        self._seed(db, "r6", "approved", repo="/repo-a")
        self._seed(db, "r7", "approved", repo="/repo-b")
        resolve_next_action_for_run("/repo-a", "quick")
        r6 = _query(db, "SELECT status FROM next_actions WHERE run_id='r6'")[0][0]
        r7 = _query(db, "SELECT status FROM next_actions WHERE run_id='r7'")[0][0]
        assert r6 == "resolved"
        assert r7 == "approved"

    def test_fresh_db_does_not_crash(self, db):
        """First-ever pass run must not crash even with no prior writes."""
        from foundry.run_index import resolve_next_action_for_run
        resolve_next_action_for_run("/repo", "quick")  # must not raise


# ---------------------------------------------------------------------------
# _fire_integrations — approval gate and needs_human behaviour
# ---------------------------------------------------------------------------

class TestFireIntegrations:
    def _agent_cfg(self, approval_required=False):
        return {"agent": {"on_failure": True, "approval_required": approval_required, "command": "echo {run_dir}"}}

    def _review_cfg(self, on_needs_human=True, review_command="echo review {run_dir}", command="echo {run_dir}"):
        return {
            "agent": {
                "on_failure": True,
                "command": command,
                "on_needs_human": on_needs_human,
                "review_command": review_command,
            }
        }

    @patch("subprocess.Popen")
    def test_agent_fires_on_fail_without_approval_required(self, popen, tmp_path):
        from foundry.runner import _fire_integrations
        _fire_integrations(self._agent_cfg(approval_required=False), _result(decision="fail"), tmp_path, tmp_path)
        popen.assert_called_once()

    @patch("subprocess.Popen")
    def test_agent_blocked_when_approval_required_and_fail(self, popen, tmp_path):
        from foundry.runner import _fire_integrations
        _fire_integrations(self._agent_cfg(approval_required=True), _result(decision="fail"), tmp_path, tmp_path)
        popen.assert_not_called()

    @patch("subprocess.Popen")
    def test_review_command_fires_on_needs_human_and_not_command(self, popen, tmp_path):
        from foundry.runner import _fire_integrations
        _fire_integrations(self._review_cfg(), _result(decision="needs_human"), tmp_path, tmp_path)
        popen.assert_called_once()
        spawned_cmd = popen.call_args.args[0][2]
        assert "review" in spawned_cmd
        assert str(tmp_path) in spawned_cmd

    @patch("subprocess.Popen")
    def test_agent_does_not_fire_on_needs_human_without_review_command(self, popen, tmp_path):
        """command must never be used as a fallback for needs_human, even if set."""
        from foundry.runner import _fire_integrations
        cfg = {"agent": {"on_failure": True, "command": "echo {run_dir}", "on_needs_human": True}}
        _fire_integrations(cfg, _result(decision="needs_human"), tmp_path, tmp_path)
        popen.assert_not_called()

    @patch("subprocess.Popen")
    def test_agent_does_not_fire_on_needs_human_without_on_needs_human_flag(self, popen, tmp_path):
        from foundry.runner import _fire_integrations
        cfg = self._review_cfg(on_needs_human=False)
        _fire_integrations(cfg, _result(decision="needs_human"), tmp_path, tmp_path)
        popen.assert_not_called()

    @patch("subprocess.Popen")
    def test_agent_does_not_fire_on_pass(self, popen, tmp_path):
        from foundry.runner import _fire_integrations
        _fire_integrations(self._review_cfg(), _result(decision="pass"), tmp_path, tmp_path)
        popen.assert_not_called()

    @patch("subprocess.Popen")
    def test_agent_does_not_fire_when_on_failure_false(self, popen, tmp_path):
        from foundry.runner import _fire_integrations
        cfg = {"agent": {"on_failure": False, "command": "echo {run_dir}"}}
        _fire_integrations(cfg, _result(decision="fail"), tmp_path, tmp_path)
        popen.assert_not_called()

    @patch("subprocess.Popen")
    def test_agent_does_not_fire_on_warn(self, popen, tmp_path):
        from foundry.runner import _fire_integrations
        _fire_integrations(self._review_cfg(), _result(decision="warn"), tmp_path, tmp_path)
        popen.assert_not_called()

    @patch("subprocess.Popen", side_effect=OSError("spawn failed"))
    def test_review_command_spawn_failure_is_caught_and_logged(self, popen, tmp_path, capsys):
        from foundry.runner import _fire_integrations
        _fire_integrations(self._review_cfg(), _result(decision="needs_human"), tmp_path, tmp_path)
        captured = capsys.readouterr()
        assert "review agent spawn failed" in captured.err


# ---------------------------------------------------------------------------
# Pruning — _ensure_schema deletes old rows but leaves recent ones alone
# ---------------------------------------------------------------------------

class TestPruning:
    def _conn(self, db_path):
        return sqlite3.connect(str(db_path))

    def test_runs_older_than_90_days_are_pruned(self, db):
        from foundry.run_index import _ensure_schema
        with self._conn(db) as conn:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO runs (run_id,repo_path,profile,decision,started_at,finished_at,"
                "gate_count,result_path,summary_path) VALUES (?,?,?,?,?,?,0,?,?)",
                ("old", "/r", "q", "pass", "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z", "/x", "/y"),
            )
        with self._conn(db) as conn:
            _ensure_schema(conn)  # second call triggers pruning
        rows = _query(db, "SELECT run_id FROM runs")
        assert rows == []

    def test_recent_run_not_pruned(self, db, tmp_path):
        from foundry.run_index import write_run_index
        write_run_index(tmp_path, _result())
        rows = _query(db, "SELECT run_id FROM runs")
        assert rows == [("run-1",)]

    def test_non_resolved_next_action_not_pruned_despite_age(self, db):
        from foundry.run_index import _ensure_schema
        with self._conn(db) as conn:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO next_actions (run_id,repo_path,profile,failed_gate_ids,status,created_at)"
                " VALUES (?,?,?,?,?,?)",
                ("r1", "/r", "q", "[]", "pending_approval", "2020-01-01T00:00:00Z"),
            )
        with self._conn(db) as conn:
            _ensure_schema(conn)
        row = _query(db, "SELECT status FROM next_actions WHERE run_id='r1'")
        assert row[0][0] == "pending_approval"  # old but not resolved — must survive

    def test_run_at_45_days_old_not_pruned(self, db):
        from foundry.run_index import _ensure_schema
        mid_ts = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._conn(db) as conn:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO runs (run_id,repo_path,profile,decision,started_at,finished_at,"
                "gate_count,result_path,summary_path) VALUES (?,?,?,?,?,?,0,?,?)",
                ("mid", "/r", "q", "pass", mid_ts, mid_ts, "/x", "/y"),
            )
        with self._conn(db) as conn:
            _ensure_schema(conn)
        rows = _query(db, "SELECT run_id FROM runs")
        assert rows == [("mid",)]

    def test_resolved_next_action_pruned_old_pending_survives(self, db):
        from foundry.run_index import _ensure_schema
        with self._conn(db) as conn:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO next_actions (run_id,repo_path,profile,failed_gate_ids,status,resolved_at)"
                " VALUES (?,?,?,?,?,?)",
                ("old-r", "/r", "q", "[]", "resolved", "2026-05-01T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO next_actions (run_id,repo_path,profile,failed_gate_ids,status,created_at)"
                " VALUES (?,?,?,?,?,?)",
                ("pending-r", "/r", "q", "[]", "pending_approval", "2026-05-01T00:00:00Z"),
            )
        with self._conn(db) as conn:
            _ensure_schema(conn)
        assert _query(db, "SELECT run_id FROM next_actions WHERE run_id='old-r'") == []
        assert _query(db, "SELECT status FROM next_actions WHERE run_id='pending-r'")[0][0] == "pending_approval"
