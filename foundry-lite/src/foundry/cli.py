import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from foundry.config import load_config
from foundry.doctor import run_doctor
from foundry.output import (
    update_latest_symlink,
    write_gitignore_if_missing,
    write_metadata_json,
)
from foundry.runner import get_git_info, run_profile
from foundry.scheduler import install_schedule, list_schedules, remove_schedule


def _repo(args) -> Path:
    return Path(getattr(args, "repo", ".") or ".").resolve()


def cmd_doctor(args):
    sys.exit(run_doctor(_repo(args)))


def cmd_run(args):
    repo = _repo(args)
    profile_name = args.profile or args.profile_flag
    if not profile_name:
        print("error: profile is required (positional or --profile NAME)", file=sys.stderr)
        sys.exit(1)

    try:
        cfg = load_config(str(repo / "foundry.yaml"))
    except (FileNotFoundError, ValueError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if profile_name not in cfg.profiles:
        print(f"Profile '{profile_name}' not found. Available: {', '.join(cfg.profiles)}", file=sys.stderr)
        sys.exit(1)

    profile = cfg.profiles[profile_name]
    debounce_seconds = float(args.debounce.rstrip("s"))

    if args.watch and args.json:
        print("Cannot use --json with --watch", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        for gate in profile.gates:
            info = gate.run or f"act {gate.act.event} -W {gate.act.workflow}"
            print(f"  {gate.id}: {info}")
        sys.exit(0)

    if args.watch:
        from foundry.watcher import FoundryWatcher
        watcher = FoundryWatcher(repo=repo, profile=profile_name, debounce=debounce_seconds)
        watcher.start()
        return

    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    foundry_dir = repo / ".foundry"
    run_dir = foundry_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_gitignore_if_missing(foundry_dir)

    from foundry.global_config import load_global_config
    gcfg = load_global_config()
    repo_alias = next((r["alias"] for r in gcfg.get("repos", []) if r.get("path") == str(repo)), None)
    alias_label = f" (repo: {repo_alias})" if repo_alias else ""
    print(f"[foundry] profile: {profile_name}{alias_label}", file=sys.stderr)
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    git = get_git_info(str(repo))
    result = run_profile(
        profile, profile_name, run_dir, repo,
        integrations_cfg=cfg.integrations,
        run_id=run_id,
        git_info=git,
        started_at=started_at,
    )
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
    elif args.evidence:
        print((run_dir / "evidence.json").read_text())
    else:
        result = json.loads((run_dir / "result.json").read_text())
        total_s = sum(g.get("duration_ms", 0) for g in result.get("gates", [])) // 1000
        print(f"{result['started_at']}  {result['profile']}  {result['decision']}  {total_s}s")


def cmd_explain(args):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set. Required for foundry explain.", file=sys.stderr)
        sys.exit(1)
    repo = _repo(args)
    if args.run:
        run_dir = repo / ".foundry" / "runs" / args.run
    else:
        run_dir = (repo / ".foundry" / "latest").resolve()
    from foundry.explain import explain_run
    print(explain_run(run_dir, args.model))
    sys.exit(0)


def cmd_init(args):
    from foundry.init_cmd import run_init
    run_init(_repo(args))


def cmd_repos(args):
    from foundry.global_config import add_repo, list_repos, load_global_config, save_global_config
    if args.repos_cmd == "add":
        add_repo(_repo(args), getattr(args, "alias", None))
        print(f"[foundry] added repo: {_repo(args)}", file=sys.stderr)
    elif args.repos_cmd == "list":
        rows = list_repos()
        if not rows:
            print("No repos registered. Use: foundry repos add")
            return
        for r in rows:
            last = f"{r['last_run']}  {r['last_decision']}" if r["last_run"] else "(no runs)  -"
            print(f"{r['alias']:<20}  {r['path']:<50}  {last}")
    elif args.repos_cmd == "remove":
        target = args.alias_or_path
        cfg = load_global_config()
        before = len(cfg.get("repos", []))
        cfg["repos"] = [r for r in cfg.get("repos", []) if r.get("alias") != target and r.get("path") != target]
        if len(cfg["repos"]) == before:
            print(f"error: no repo matching '{target}'", file=sys.stderr)
            sys.exit(1)
        save_global_config(cfg)
        print(f"[foundry] removed repo: {target}", file=sys.stderr)


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
    sub.add_parser("init", help="Generate foundry.yaml from detected gates")

    run_p = sub.add_parser("run", help="Run a profile")
    run_p.add_argument("profile", nargs="?", default=None, help="Profile name from foundry.yaml")
    run_p.add_argument("--profile", dest="profile_flag", default=None, metavar="NAME",
                       help="Alias for positional profile (for scripting convenience)")
    run_p.add_argument("--json", action="store_true", help="Print result.json to stdout")
    run_p.add_argument("--dry-run", action="store_true", help="Show gates without running")
    run_p.add_argument("--watch", action="store_true", help="Re-run on file changes")
    run_p.add_argument("--debounce", default="2s", help="Debounce interval (e.g. 2s)")

    explain_p = sub.add_parser("explain", help="Explain latest run with AI")
    explain_p.add_argument("--latest", action="store_true", default=True, help="Use latest run (default)")
    explain_p.add_argument("--run", default=None, metavar="RUN_ID", help="Specific run ID")
    explain_p.add_argument("--model", default="claude-haiku-4-5-20251001", help="Model to use")

    latest_p = sub.add_parser("latest", help="Show latest run result")
    latest_p.add_argument("--json", action="store_true")
    latest_p.add_argument("--summary", action="store_true")
    latest_p.add_argument("--evidence", action="store_true")

    sched_p = sub.add_parser("schedule", help="Manage schedules")
    sched_sub = sched_p.add_subparsers(dest="schedule_cmd")
    install_p = sched_sub.add_parser("install")
    install_p.add_argument("name")
    remove_p = sched_sub.add_parser("remove")
    remove_p.add_argument("name")
    sched_sub.add_parser("list")

    repos_p = sub.add_parser("repos", help="Manage tracked repos")
    repos_sub = repos_p.add_subparsers(dest="repos_cmd")
    repos_add_p = repos_sub.add_parser("add", help="Track current repo")
    repos_add_p.add_argument("--alias", default=None, metavar="NAME", help="Short name for this repo")
    repos_sub.add_parser("list", help="List tracked repos")
    repos_remove_p = repos_sub.add_parser("remove", help="Remove a tracked repo")
    repos_remove_p.add_argument("alias_or_path", help="Alias or path to remove")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {"doctor": cmd_doctor, "init": cmd_init, "run": cmd_run, "latest": cmd_latest, "explain": cmd_explain, "schedule": cmd_schedule, "repos": cmd_repos}
    if args.command in dispatch:
        if args.command == "schedule" and args.schedule_cmd is None:
            sched_p.print_help()
            sys.exit(0)
        if args.command == "repos" and args.repos_cmd is None:
            repos_p.print_help()
            sys.exit(0)
        dispatch[args.command](args)
    else:
        parser.print_help()
        sys.exit(1)
