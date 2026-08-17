"""Tests for the explain_run dedup fix (foundry-dao.5).

Covers: runner.run_profile / runner._fire_integrations. On a fail Decision,
run_profile used to call explain_run once for the next_actions record
(default model claude-haiku-4-5-20251001) and _fire_integrations called it
again for explanation.md when integrations.explain.on_failure was configured
(default model gemini-3.5-flash) — two LLM calls, two silently different
default models, for the same run. Now the explanation is computed once and
reused for both, under a single shared default model.
"""
import subprocess
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from foundry.runner import DEFAULT_EXPLAIN_MODEL, prepare_and_run_profile


FAILING_YAML_TEMPLATE = """\
version: 1
profiles:
  ci:
    gates:
      - id: bad
        run: "false"
integrations:
  explain:
    on_failure: true{model_line}
"""

FAILING_YAML_NO_EXPLAIN = """\
version: 1
profiles:
  ci:
    gates:
      - id: bad
        run: "false"
"""

NEEDS_HUMAN_YAML = """\
version: 1
profiles:
  ci:
    gates:
      - id: review
        run: "echo FOUNDRY_NEEDS_HUMAN"
integrations:
  explain:
    on_failure: true
"""

PASSING_YAML = """\
version: 1
profiles:
  ci:
    gates:
      - id: ok
        run: "true"
integrations:
  explain:
    on_failure: true
"""


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def _next_action_row(db_path: Path, run_id: str):
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(
            "SELECT explanation FROM next_actions WHERE run_id = ?", (run_id,)
        ).fetchone()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _init_repo(r)
    return r


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Redirect FOUNDRY_DB to a temp file, as in test_run_index.py."""
    import foundry.run_index as mod
    db_path = tmp_path / "foundry.db"
    monkeypatch.setattr(mod, "FOUNDRY_DB", db_path)
    return db_path


class TestFailDecisionExplainDeduped:
    def test_explain_run_called_exactly_once_with_integration_configured(self, repo, db):
        (repo / "foundry.yaml").write_text(FAILING_YAML_TEMPLATE.format(model_line=""))

        with patch("foundry.explain.explain_run", return_value="the explanation") as mock_explain, \
             patch("foundry.explain.extract_next_action", return_value="do the thing"):
            result = prepare_and_run_profile("ci", repo)

        assert result["decision"] == "fail"
        assert mock_explain.call_count == 1

    def test_same_explanation_used_for_next_actions_and_explanation_md(self, repo, db):
        (repo / "foundry.yaml").write_text(FAILING_YAML_TEMPLATE.format(model_line=""))

        with patch("foundry.explain.explain_run", return_value="the shared explanation") as mock_explain, \
             patch("foundry.explain.extract_next_action", return_value="do the thing"):
            result = prepare_and_run_profile("ci", repo)

        run_dir = repo / ".foundry" / "runs" / result["run_id"]
        assert (run_dir / "explanation.md").read_text() == "the shared explanation"
        row = _next_action_row(db, result["run_id"])
        assert row is not None
        assert row[0] == "the shared explanation"
        assert mock_explain.call_count == 1

    def test_explain_run_called_once_with_no_explain_integration_configured(self, repo, db):
        (repo / "foundry.yaml").write_text(FAILING_YAML_NO_EXPLAIN)

        with patch("foundry.explain.explain_run", return_value="lone explanation") as mock_explain, \
             patch("foundry.explain.extract_next_action", return_value="do the thing"):
            result = prepare_and_run_profile("ci", repo)

        assert result["decision"] == "fail"
        assert mock_explain.call_count == 1
        run_dir = repo / ".foundry" / "runs" / result["run_id"]
        assert not (run_dir / "explanation.md").exists()
        row = _next_action_row(db, result["run_id"])
        assert row[0] == "lone explanation"

    def test_single_default_model_used_when_not_configured_in_yaml(self, repo, db):
        (repo / "foundry.yaml").write_text(FAILING_YAML_TEMPLATE.format(model_line=""))

        with patch("foundry.explain.explain_run", return_value="x") as mock_explain, \
             patch("foundry.explain.extract_next_action", return_value="y"):
            prepare_and_run_profile("ci", repo)

        mock_explain.assert_called_once()
        _, model = mock_explain.call_args[0]
        assert model == DEFAULT_EXPLAIN_MODEL


class TestPassWarnDecisionNoExplainCall:
    def test_pass_decision_does_not_call_explain_run(self, repo, db):
        (repo / "foundry.yaml").write_text(PASSING_YAML)

        with patch("foundry.explain.explain_run") as mock_explain:
            result = prepare_and_run_profile("ci", repo)

        assert result["decision"] == "pass"
        mock_explain.assert_not_called()


class TestNeedsHumanExplainBehaviorPreserved:
    def test_needs_human_still_calls_explain_run_once(self, repo, db, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        (repo / "foundry.yaml").write_text(NEEDS_HUMAN_YAML)

        with patch("foundry.explain.explain_run", return_value="human review needed") as mock_explain:
            result = prepare_and_run_profile("ci", repo)

        assert result["decision"] == "needs_human"
        assert mock_explain.call_count == 1
        run_dir = repo / ".foundry" / "runs" / result["run_id"]
        assert (run_dir / "explanation.md").read_text() == "human review needed"
