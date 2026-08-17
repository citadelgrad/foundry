"""Tests for Scheduler/Daemon overlap detection (foundry-dao.6).

Covers: scheduler.install_schedule and daemon.daemon_start each gaining a
non-blocking, advisory-only warning when the other scheduling mechanism is
also configured for the same repo — since scheduler.py installs OS-level
launchd/cron entries per Schedule while daemon.py independently polls
registered repos' foundry.yaml schedules in-process, and neither knows
about the other, so a Profile can silently fire twice.
"""
from foundry.config import FoundryConfig, ScheduleConfig
import foundry.daemon as daemon_mod
import foundry.scheduler as scheduler_mod


def _config(schedules: dict) -> FoundryConfig:
    return FoundryConfig(version=1, profiles={}, schedules=schedules)


class TestInstallScheduleWarnsOnDaemonOverlap:
    def test_warns_when_repo_registered_for_daemon_polling(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(scheduler_mod, "SCHEDULE_REGISTRY", tmp_path / "registry.json")
        monkeypatch.setattr(scheduler_mod, "_install_launchd", lambda *a, **k: None)
        monkeypatch.setattr(scheduler_mod, "_install_cron", lambda *a, **k: None)
        monkeypatch.setattr(
            scheduler_mod, "list_repos",
            lambda: [{"alias": "a", "path": "/repo/a", "last_run": None, "last_decision": None}],
        )
        cfg = _config({"nightly": ScheduleConfig(profile="ci", cron="0 2 * * *")})

        scheduler_mod.install_schedule("nightly", cfg, "/repo/a")

        err = capsys.readouterr().err
        assert "/repo/a" in err
        assert "nightly" in err
        assert "daemon" in err.lower()

    def test_schedule_still_installed_despite_warning(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(scheduler_mod, "SCHEDULE_REGISTRY", tmp_path / "registry.json")
        monkeypatch.setattr(scheduler_mod, "_install_launchd", lambda *a, **k: None)
        monkeypatch.setattr(scheduler_mod, "_install_cron", lambda *a, **k: None)
        monkeypatch.setattr(
            scheduler_mod, "list_repos",
            lambda: [{"alias": "a", "path": "/repo/a", "last_run": None, "last_decision": None}],
        )
        cfg = _config({"nightly": ScheduleConfig(profile="ci", cron="0 2 * * *")})

        scheduler_mod.install_schedule("nightly", cfg, "/repo/a")

        registry = scheduler_mod._load_registry()
        assert "nightly" in registry
        assert registry["nightly"]["repo"] == "/repo/a"

    def test_no_warning_when_repo_not_registered_for_daemon_polling(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(scheduler_mod, "SCHEDULE_REGISTRY", tmp_path / "registry.json")
        monkeypatch.setattr(scheduler_mod, "_install_launchd", lambda *a, **k: None)
        monkeypatch.setattr(scheduler_mod, "_install_cron", lambda *a, **k: None)
        monkeypatch.setattr(scheduler_mod, "list_repos", lambda: [])
        cfg = _config({"nightly": ScheduleConfig(profile="ci", cron="0 2 * * *")})

        scheduler_mod.install_schedule("nightly", cfg, "/repo/a")

        err = capsys.readouterr().err
        assert err == ""

    def test_daemon_lookup_failure_does_not_raise_or_block_install(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler_mod, "SCHEDULE_REGISTRY", tmp_path / "registry.json")
        monkeypatch.setattr(scheduler_mod, "_install_launchd", lambda *a, **k: None)
        monkeypatch.setattr(scheduler_mod, "_install_cron", lambda *a, **k: None)

        def _boom():
            raise RuntimeError("global config unreadable")

        monkeypatch.setattr(scheduler_mod, "list_repos", _boom)
        cfg = _config({"nightly": ScheduleConfig(profile="ci", cron="0 2 * * *")})

        scheduler_mod.install_schedule("nightly", cfg, "/repo/a")  # must not raise

        registry = scheduler_mod._load_registry()
        assert "nightly" in registry


class TestDaemonStartWarnsOnScheduleOverlap:
    def _patch_install_targets(self, monkeypatch, tmp_path):
        monkeypatch.setattr(daemon_mod, "_PLIST", tmp_path / "com.foundry.daemon.plist")
        monkeypatch.setattr(daemon_mod, "_SYSTEMD", tmp_path / "foundry-daemon.service")
        monkeypatch.setattr(daemon_mod.subprocess, "run", lambda *a, **k: None)

    def test_warns_when_repo_profile_pair_overlaps_installed_schedule(self, tmp_path, monkeypatch, capsys):
        self._patch_install_targets(monkeypatch, tmp_path)
        monkeypatch.setattr(
            daemon_mod, "_load_schedule_registry",
            lambda: {"nightly": {"profile": "ci", "cron": "0 2 * * *", "repo": "/repo/a", "os": "darwin"}},
        )
        monkeypatch.setattr(
            daemon_mod, "list_repos",
            lambda: [{"alias": "a", "path": "/repo/a", "last_run": None, "last_decision": None}],
        )
        monkeypatch.setattr(
            daemon_mod, "load_config",
            lambda path: _config({"nightly": ScheduleConfig(profile="ci", cron="0 2 * * *")}),
        )

        daemon_mod.daemon_start()

        err = capsys.readouterr().err
        assert "/repo/a" in err
        assert "nightly" in err
        assert "ci" in err

    def test_daemon_start_succeeds_despite_warning(self, tmp_path, monkeypatch):
        self._patch_install_targets(monkeypatch, tmp_path)
        monkeypatch.setattr(
            daemon_mod, "_load_schedule_registry",
            lambda: {"nightly": {"profile": "ci", "cron": "0 2 * * *", "repo": "/repo/a", "os": "darwin"}},
        )
        monkeypatch.setattr(
            daemon_mod, "list_repos",
            lambda: [{"alias": "a", "path": "/repo/a", "last_run": None, "last_decision": None}],
        )
        monkeypatch.setattr(
            daemon_mod, "load_config",
            lambda path: _config({"nightly": ScheduleConfig(profile="ci", cron="0 2 * * *")}),
        )

        daemon_mod.daemon_start()  # must not raise

    def test_no_warning_when_no_overlap(self, tmp_path, monkeypatch, capsys):
        self._patch_install_targets(monkeypatch, tmp_path)
        monkeypatch.setattr(
            daemon_mod, "_load_schedule_registry",
            lambda: {"nightly": {"profile": "ci", "cron": "0 2 * * *", "repo": "/repo/other", "os": "darwin"}},
        )
        monkeypatch.setattr(
            daemon_mod, "list_repos",
            lambda: [{"alias": "a", "path": "/repo/a", "last_run": None, "last_decision": None}],
        )
        monkeypatch.setattr(
            daemon_mod, "load_config",
            lambda path: _config({"nightly": ScheduleConfig(profile="ci", cron="0 2 * * *")}),
        )

        daemon_mod.daemon_start()

        err = capsys.readouterr().err
        assert "may fire twice" not in err

    def test_no_overlap_when_no_schedules_installed(self, tmp_path, monkeypatch, capsys):
        self._patch_install_targets(monkeypatch, tmp_path)
        monkeypatch.setattr(daemon_mod, "_load_schedule_registry", lambda: {})
        monkeypatch.setattr(daemon_mod, "list_repos", lambda: [])

        daemon_mod.daemon_start()

        err = capsys.readouterr().err
        assert "may fire twice" not in err

    def test_registry_lookup_failure_does_not_raise_or_block_start(self, tmp_path, monkeypatch):
        self._patch_install_targets(monkeypatch, tmp_path)

        def _boom():
            raise RuntimeError("registry file corrupt")

        monkeypatch.setattr(daemon_mod, "_load_schedule_registry", _boom)

        daemon_mod.daemon_start()  # must not raise
