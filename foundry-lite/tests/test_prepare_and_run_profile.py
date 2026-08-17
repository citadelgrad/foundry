"""Tests for the shared prepare_and_run_profile seam (foundry-dao.1).

Covers: runner.prepare_and_run_profile (the single function used by both
`foundry run` and `foundry run --watch`), and watcher.FoundryWatcher, which
used to call run_profile() with the wrong signature and crash with a
TypeError the moment `foundry run --watch` fired.
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from foundry.runner import ProfileNotFoundError, prepare_and_run_profile
from foundry.watcher import FoundryWatcher


PASSING_YAML = """\
version: 1
profiles:
  ci:
    gates:
      - id: ok
        run: "true"
  other:
    gates:
      - id: ok
        run: "true"
"""

FAILING_YAML = """\
version: 1
profiles:
  ci:
    gates:
      - id: bad
        run: "false"
"""


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


# ---------------------------------------------------------------------------
# prepare_and_run_profile
# ---------------------------------------------------------------------------

class TestPrepareAndRunProfile:
    def test_happy_path_runs_profile_and_writes_run_dir(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "foundry.yaml").write_text(PASSING_YAML)

        result = prepare_and_run_profile("ci", repo)

        assert result["decision"] == "pass"
        run_dir = repo / ".foundry" / "runs" / result["run_id"]
        assert run_dir.is_dir()
        assert (run_dir / "result.json").exists()
        assert (run_dir / "metadata.json").exists()

    def test_gitignore_seeded_exactly_once(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "foundry.yaml").write_text(PASSING_YAML)

        prepare_and_run_profile("ci", repo)
        gitignore = repo / ".foundry" / ".gitignore"
        assert gitignore.exists()
        first_contents = gitignore.read_text()

        prepare_and_run_profile("ci", repo)
        assert gitignore.read_text() == first_contents

    def test_latest_symlink_updated(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "foundry.yaml").write_text(PASSING_YAML)

        result = prepare_and_run_profile("ci", repo)
        latest = repo / ".foundry" / "latest"
        assert latest.is_symlink()
        assert latest.resolve() == (repo / ".foundry" / "runs" / result["run_id"]).resolve()

    def test_unknown_profile_raises_with_available_list(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "foundry.yaml").write_text(PASSING_YAML)

        with pytest.raises(ProfileNotFoundError) as exc_info:
            prepare_and_run_profile("nope", repo)
        assert exc_info.value.profile_name == "nope"
        assert set(exc_info.value.available) == {"ci", "other"}

    def test_missing_foundry_yaml_raises_file_not_found(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        with pytest.raises(FileNotFoundError):
            prepare_and_run_profile("ci", repo)

    def test_invalid_foundry_yaml_raises_value_error(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "foundry.yaml").write_text("version: 2\n")

        with pytest.raises(ValueError):
            prepare_and_run_profile("ci", repo)


# ---------------------------------------------------------------------------
# FoundryWatcher — confirms the TypeError from the old run_profile(...) call
# is gone, and that error handling differs between the initial (fatal) run
# and rerun-on-change (non-fatal) runs.
# ---------------------------------------------------------------------------

class TestFoundryWatcherRunOnce:
    def test_run_once_calls_prepare_and_run_profile_with_correct_args(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        watcher = FoundryWatcher(repo=repo, profile="ci", debounce=0.1)

        with patch("foundry.watcher.prepare_and_run_profile") as mock_run:
            watcher._run_once(fatal=True)
        mock_run.assert_called_once_with("ci", repo)

    def test_fatal_unknown_profile_exits_1(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        watcher = FoundryWatcher(repo=repo, profile="nope", debounce=0.1)

        with patch(
            "foundry.watcher.prepare_and_run_profile",
            side_effect=ProfileNotFoundError("nope", ["ci"]),
        ):
            with pytest.raises(SystemExit) as exc_info:
                watcher._run_once(fatal=True)
        assert exc_info.value.code == 1

    def test_non_fatal_unknown_profile_does_not_exit(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        watcher = FoundryWatcher(repo=repo, profile="nope", debounce=0.1)

        with patch(
            "foundry.watcher.prepare_and_run_profile",
            side_effect=ProfileNotFoundError("nope", ["ci"]),
        ):
            watcher._run_once(fatal=False)  # must not raise SystemExit

    def test_watch_end_to_end_no_type_error(self, tmp_path):
        """Regression test for the original bug: watcher.start() used to call
        run_profile(self.profile, repo_path=self.repo), which raised TypeError
        immediately because run_profile's signature requires profile_name/run_dir.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "foundry.yaml").write_text(PASSING_YAML)
        watcher = FoundryWatcher(repo=repo, profile="ci", debounce=0.1)

        watcher._run_once(fatal=True)  # must not raise TypeError

        run_dirs = list((repo / ".foundry" / "runs").iterdir())
        assert len(run_dirs) == 1
