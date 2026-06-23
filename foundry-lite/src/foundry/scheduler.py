import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from croniter import CroniterBadCronError, croniter

from foundry.config import FoundryConfig

SCHEDULE_REGISTRY = Path.home() / ".foundry_schedules.json"


def install_schedule(name: str, config: FoundryConfig, repo_path: str) -> None:
    if name not in config.schedules:
        raise ValueError(f'Schedule "{name}" not found in foundry.yaml')

    schedule = config.schedules[name]
    try:
        croniter(schedule.cron)
    except (CroniterBadCronError, ValueError) as e:
        raise ValueError(f'Invalid cron expression "{schedule.cron}": {e}')
    foundry_bin = shutil.which("foundry") or f"{sys.executable} -m foundry"

    if platform.system() == "Darwin":
        _install_launchd(name, schedule.profile, schedule.cron, repo_path, foundry_bin)
    else:
        _install_cron(name, schedule.profile, schedule.cron, repo_path, foundry_bin)

    _save_registry(name, schedule.profile, schedule.cron, repo_path)
    print(f'Installed schedule "{name}" ({schedule.cron})')


def _install_launchd(name, profile, cron, repo_path, foundry_bin):
    parts = cron.strip().split()
    minute, hour, day, month, weekday = parts

    def _calendar_field(key: str, value: str) -> str:
        if value == "*":
            return ""
        if not value.isdigit():
            raise ValueError(
                f"launchd schedules only support numeric or '*' cron fields; got {key}={value!r}"
            )
        return f"    <key>{key}</key>\n    <integer>{value}</integer>\n"

    calendar = (
        _calendar_field("Minute", minute)
        + _calendar_field("Hour", hour)
        + _calendar_field("Day", day)
        + _calendar_field("Month", month)
        + _calendar_field("Weekday", weekday)
    )

    plist_path = Path.home() / "Library" / "LaunchAgents" / f"com.foundry.{name}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.foundry.{name}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{foundry_bin}</string>
    <string>run</string>
    <string>{profile}</string>
    <string>--repo</string>
    <string>{repo_path}</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
{calendar.rstrip()}
  </dict>
  <key>StandardOutPath</key>
  <string>/tmp/foundry-{name}.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/foundry-{name}.log</string>
</dict>
</plist>"""
    plist_path.write_text(plist_content)
    subprocess.run(["launchctl", "load", str(plist_path)], check=False)


def _install_cron(name, profile, cron, repo_path, foundry_bin):
    cron_line = f"{cron}  {foundry_bin} run {profile} --repo {repo_path} >> /tmp/foundry-{name}.log 2>&1  # foundry:{name}"
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout if result.returncode == 0 else ""
    new_crontab = existing.rstrip() + "\n" + cron_line + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)


def list_schedules() -> None:
    registry = _load_registry()
    if not registry:
        print("No schedules installed.")
        return
    for name, info in registry.items():
        print(f"{name:<15} {info['profile']:<10} {info['cron']:<15} {info['repo']}")


def remove_schedule(name: str) -> None:
    registry = _load_registry()
    if name not in registry:
        print(f'Schedule "{name}" not found.')
        return

    if platform.system() == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"com.foundry.{name}.plist"
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], check=False)
            plist.unlink()
    else:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode == 0:
            lines = [l for l in result.stdout.splitlines() if f"# foundry:{name}" not in l]
            subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=False)

    del registry[name]
    _save_registry_dict(registry)
    print(f'Removed schedule "{name}"')


def _load_registry() -> dict:
    if not SCHEDULE_REGISTRY.exists():
        return {}
    return json.loads(SCHEDULE_REGISTRY.read_text())


def _save_registry(name, profile, cron, repo_path) -> None:
    registry = _load_registry()
    registry[name] = {"profile": profile, "cron": cron, "repo": repo_path, "os": platform.system().lower()}
    _save_registry_dict(registry)


def _save_registry_dict(registry: dict) -> None:
    SCHEDULE_REGISTRY.write_text(json.dumps(registry, indent=2))
