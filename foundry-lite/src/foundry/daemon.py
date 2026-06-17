import platform
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from croniter import CroniterBadCronError, croniter

from foundry.config import load_config
from foundry.global_config import list_repos

_LAUNCHD_LABEL = "com.foundry.daemon"
_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"
_SYSTEMD = Path.home() / ".config" / "systemd" / "user" / "foundry-daemon.service"


def _foundry_argv() -> list[str]:
    binary = shutil.which("foundry")
    return [binary] if binary else [sys.executable, "-m", "foundry"]


def _next_fire(cron: str, after: datetime) -> datetime | None:
    try:
        return croniter(cron, after).get_next(datetime)
    except (CroniterBadCronError, ValueError):
        return None


def run_daemon_loop() -> None:
    in_flight: set[tuple[str, str]] = set()
    lock = threading.Lock()
    last_fired: dict[tuple[str, str], datetime] = {}

    print("[foundry daemon] started", file=sys.stderr)

    while True:
        now = datetime.now(timezone.utc)
        repos = list_repos()

        jobs: list[tuple[str, str, str]] = []  # (cron, repo_path, profile)
        for repo in repos:
            try:
                cfg = load_config(str(Path(repo["path"]) / "foundry.yaml"))
            except Exception:
                continue
            for sched in cfg.schedules.values():
                jobs.append((sched.cron, repo["path"], sched.profile))

        next_fires: list[datetime] = []
        for cron, repo_path, profile in jobs:
            key = (repo_path, profile)
            # use last_fired as base so we don't double-fire; default to 61s ago on first run
            base = last_fired.get(key, now - timedelta(seconds=61))
            nf = _next_fire(cron, base)
            if nf is None:
                continue
            next_fires.append(nf)
            if nf > now:
                continue

            last_fired[key] = nf
            with lock:
                if key in in_flight:
                    print(f"[foundry daemon] skip {profile}@{repo_path}: still running", file=sys.stderr)
                    continue
                in_flight.add(key)

            def _run(key=key, repo_path=repo_path, profile=profile):
                try:
                    subprocess.run(
                        _foundry_argv() + ["run", profile, "--repo", repo_path],
                        check=False,
                    )
                finally:
                    with lock:
                        in_flight.discard(key)

            threading.Thread(target=_run, daemon=True).start()

        future = [f for f in next_fires if f > now]
        sleep_secs = (min(future) - now).total_seconds() if future else 60.0
        time.sleep(min(max(sleep_secs, 1.0), 60.0))


def daemon_start() -> None:
    argv = _foundry_argv() + ["daemon", "run"]
    if platform.system() == "Darwin":
        args_xml = "".join(f"    <string>{a}</string>\n" for a in argv)
        _PLIST.parent.mkdir(parents=True, exist_ok=True)
        _PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{_LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{args_xml}  </array>
  <key>KeepAlive</key>
  <true/>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/foundry-daemon.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/foundry-daemon.log</string>
</dict>
</plist>""")
        subprocess.run(["launchctl", "load", str(_PLIST)], check=False)
        print(f"[foundry daemon] installed and started (launchd)\n  log: /tmp/foundry-daemon.log", file=sys.stderr)
    else:
        _SYSTEMD.parent.mkdir(parents=True, exist_ok=True)
        _SYSTEMD.write_text(f"""[Unit]
Description=Foundry daemon

[Service]
ExecStart={' '.join(argv)}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
""")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", "foundry-daemon"], check=False)
        print(f"[foundry daemon] installed and started (systemd user)\n  log: journalctl --user -u foundry-daemon -f", file=sys.stderr)


def daemon_stop() -> None:
    if platform.system() == "Darwin":
        if _PLIST.exists():
            subprocess.run(["launchctl", "unload", str(_PLIST)], check=False)
            _PLIST.unlink()
            print("[foundry daemon] stopped and removed", file=sys.stderr)
        else:
            print("[foundry daemon] not installed", file=sys.stderr)
    else:
        subprocess.run(["systemctl", "--user", "disable", "--now", "foundry-daemon"], check=False)
        if _SYSTEMD.exists():
            _SYSTEMD.unlink()
        print("[foundry daemon] stopped and removed", file=sys.stderr)


def daemon_status() -> None:
    if platform.system() == "Darwin":
        if not _PLIST.exists():
            print("foundry daemon: not installed")
            return
        result = subprocess.run(
            ["launchctl", "list", _LAUNCHD_LABEL],
            capture_output=True, text=True,
        )
        running = result.returncode == 0
        print(f"foundry daemon: {'running' if running else 'installed but not running'}")
        print(f"  plist: {_PLIST}")
        print(f"  log:   /tmp/foundry-daemon.log")
    else:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "foundry-daemon"],
            capture_output=True, text=True,
        )
        print(f"foundry daemon: {result.stdout.strip()}")
        print(f"  unit: {_SYSTEMD}")
        print(f"  log:  journalctl --user -u foundry-daemon -f")
