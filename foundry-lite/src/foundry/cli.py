import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from foundry.config import load_config
from foundry.doctor import run_doctor
from foundry.output import (
    update_latest_symlink,
    write_gitignore_if_missing,
    write_metadata_json,
    write_result_json,
    write_summary_md,
)
from foundry.runner import RunResult, derive_decision, get_git_info, run_profile
from foundry.scheduler import install_schedule, list_schedules, remove_schedule


def _repo(args) -> Path:
    return Path(getattr(args, "repo", ".") or ".").resolve()


def cmd_doctor(args):
    sys.exit(run_doctor(_repo(args)))


def cmd_run(args):
    repo = _repo(args)
    profile_name = args.profile

    try:
        cfg = load_config(str(repo / "foundry.yaml"))
    except (FileNotFoundError, ValueError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if profile_name not in cfg.profiles:
        print(f"Profile '{profile_name}' not found. Available: {', '.join(cfg.profiles)}", file=sys.stderr)
        sys.exit(1)

    profile = cfg.profiles[profile_name]

    if args.dry_run:
        for gate in profile.gates:
            info = gate.run or f"act {gate.act.event} -W {gate.act.workflow}"
            print(f"  {gate.id}: {info}")
        sys.exit(0)

    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    foundry_dir = repo / ".foundry"
    run_dir = foundry_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_gitignore_if_missing(foundry_dir)

    print(f"[foundry] profile: {profile_name}", file=sys.stderr)
    started_at = datetime.now(timezone.utc).isoformat()
    gate_results = run_profile(profile, profile_name, run_dir, repo)
    finished_at = datetime.now(timezone.utc).isoformat()

    git = get_git_info(str(repo))
    decision = derive_decision(gate_results)
    exit_code = 0 if decision in ("pass", "warn") else 1
    run_result = RunResult(
        run_id=run_id, profile=profile_name, decision=decision, exit_code=exit_code,
        started_at=started_at, finished_at=finished_at, repo_path=str(repo),
        commit=git["commit"], branch=git["branch"], dirty=git["dirty"],
        gates=gate_results,
    )
    result = write_result_json(run_result, run_dir)
    write_summary_md(run_result, run_dir)
    write_metadata_json(run_dir)
    update_latest_symlink(run_dir)

    decision = result["decision"]
    print(f"[foundry] decision: {decision}", file=sys.stderr)
    print(f"[foundry] evidence: {run_dir}", file=sys.stderr)

    if args.json:
        print(json.dumps(result, indent=2))

    sys.exit(0 if decision in ("pass", "warn") else 1)


def cmd_latest(args):
    repo = _repo(args)
    latest = repo / ".foundry" / "latest"
    if not latest.exists():
        print("No runs found.", file=sys.stderr)
        sys.exit(1)

    run_dir = latest.resolve()
    if args.json:
        print((run_dir / "result.json").read_text())
    elif args.summary:
        print((run_dir / "summary.md").read_text())
    else:
        result = json.loads((run_dir / "result.json").read_text())
        total_s = sum(g.get("duration_ms", 0) for g in result.get("gates", [])) // 1000
        print(f"{result['started_at']}  {result['profile']}  {result['decision']}  {total_s}s")


def cmd_schedule(args):
    repo = _repo(args)
    if args.schedule_cmd == "remove":
        remove_schedule(args.name)
        return
    if args.schedule_cmd == "list":
        list_schedules()
        return
    try:
        cfg = load_config(str(repo / "foundry.yaml"))
    except (FileNotFoundError, ValueError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    if args.schedule_cmd == "install":
        try:
            install_schedule(args.name, cfg, str(repo))
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
    elif args.schedule_cmd == "list":
        list_schedules()


def main():
    # ponytail: shared parent so --repo works after any subcommand
    parser = argparse.ArgumentParser(prog="foundry", description="Local quality gate runner")
    parser.add_argument("--repo", default=".", help="Path to repository")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="Check environment")

    run_p = sub.add_parser("run", help="Run a profile")
    run_p.add_argument("profile", help="Profile name from foundry.yaml")
    run_p.add_argument("--json", action="store_true", help="Print result.json to stdout")
    run_p.add_argument("--dry-run", action="store_true", help="Show gates without running")

    latest_p = sub.add_parser("latest", help="Show latest run result")
    latest_p.add_argument("--json", action="store_true")
    latest_p.add_argument("--summary", action="store_true")

    sched_p = sub.add_parser("schedule", help="Manage schedules")
    sched_sub = sched_p.add_subparsers(dest="schedule_cmd")
    install_p = sched_sub.add_parser("install")
    install_p.add_argument("name")
    remove_p = sched_sub.add_parser("remove")
    remove_p.add_argument("name")
    sched_sub.add_parser("list")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {"doctor": cmd_doctor, "run": cmd_run, "latest": cmd_latest, "schedule": cmd_schedule}
    if args.command in dispatch:
        if args.command == "schedule" and args.schedule_cmd is None:
            sched_p.print_help()
            sys.exit(0)
        dispatch[args.command](args)
    else:
        parser.print_help()
        sys.exit(1)
