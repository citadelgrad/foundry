"""Tests for dagger gate execution merged into run_gate (foundry-dao.2).

Covers: runner.run_gate dispatching `dagger:` gates through the same
subprocess/log-cap/FOUNDRY_NEEDS_HUMAN path used by shell/act gates, using
the same terminate()-then-kill() timeout sequence. dagger_runner.py no
longer exists.
"""
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from foundry.config import DaggerBlock, GateConfig
from foundry.runner import LOG_SIZE_CAP, run_gate


def _dagger_gate(timeout="10m", allow_failure=False, args=None):
    return GateConfig(
        id="dg",
        dagger=DaggerBlock(module="./ci", function="test", args=args or []),
        timeout=timeout,
        allow_failure=allow_failure,
    )


def _write_fake_dagger(bin_dir: Path, script: str) -> None:
    path = bin_dir / "dagger"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class TestDaggerRunnerModuleRemoved:
    def test_dagger_runner_module_deleted(self):
        import foundry
        assert not (Path(foundry.__file__).parent / "dagger_runner.py").exists()

    def test_nothing_imports_dagger_runner(self):
        import foundry.runner
        import foundry.cli
        import foundry.watcher
        # Import succeeding at all proves neither module references the
        # deleted dagger_runner module at import time.
        assert True


class TestDaggerGateViaRunGate:
    def test_dagger_gate_passes(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_dagger(bin_dir, "#!/bin/sh\necho 'build ok'\nexit 0\n")
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = run_gate(_dagger_gate(), log_dir, tmp_path)

        assert result.status == "passed"
        assert result.runner == "dagger"
        assert result.exit_code == 0
        assert result.command == "dagger call -m ./ci test"
        assert "build ok" in (log_dir / "dg.log").read_text()

    def test_dagger_gate_needs_human(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_dagger(
            bin_dir, "#!/bin/sh\necho 'FOUNDRY_NEEDS_HUMAN: check this'\nexit 1\n"
        )
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = run_gate(_dagger_gate(), log_dir, tmp_path)

        assert result.status == "needs_human"

    def test_dagger_gate_log_capped(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_dagger(
            bin_dir,
            "#!/bin/sh\nhead -c 700000 /dev/zero | tr '\\0' 'x'\nexit 0\n",
        )
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = run_gate(_dagger_gate(), log_dir, tmp_path)

        assert result.status == "passed"
        assert result.log_truncated is True
        assert len((log_dir / "dg.log").read_bytes()) == LOG_SIZE_CAP

    def test_dagger_gate_missing_binary_returns_exit_127(self, tmp_path, monkeypatch):
        empty_bin = tmp_path / "empty"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        result = run_gate(_dagger_gate(), log_dir, tmp_path)

        assert result.status == "failed"
        assert result.exit_code == 127


class TestDaggerGateTimeoutEscalation:
    def test_timeout_terminates_then_kills(self, tmp_path):
        """Same terminate()-then-kill() sequence as a shell/run gate."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        gate = _dagger_gate(timeout="1s")

        mock_proc = MagicMock()
        mock_proc.stdout.read.side_effect = [b""]
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="dagger", timeout=1),
            subprocess.TimeoutExpired(cmd="dagger", timeout=5),
            None,
        ]
        mock_proc.returncode = None

        with patch("foundry.runner.subprocess.Popen", return_value=mock_proc):
            result = run_gate(gate, log_dir, tmp_path)

        assert result.status == "timed_out"
        assert mock_proc.terminate.called
        assert mock_proc.kill.called
        terminate_index = mock_proc.method_calls.index(call.terminate())
        kill_index = mock_proc.method_calls.index(call.kill())
        assert terminate_index < kill_index
