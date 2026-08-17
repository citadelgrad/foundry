import argparse
import json
import os
import sys
from pathlib import Path

from foundry.config import load_config
from foundry.doctor import run_doctor
from foundry.runner import ProfileNotFoundError, prepare_and_run_profile
from foundry.scheduler import install_schedule, list_schedules, remove_schedule


def _repo(args) -> Path:
    return Path(getattr(args, "repo", ".") or ".").resolve()


def cmd_doctor(args):
    sys.exit(run_doctor(_repo(args)))


def _print_config_error(e_type, e, repo):
    if e_type is FileNotFoundError:
        print(f"error: foundry.yaml not found in {repo}\n\nRun 'foundry init' to generate one from detected gates.", file=sys.stderr)
    elif e_type is ProfileNotFoundError:
        available = ", ".join(e.available) or "(none)"
        print(f"error: profile '{e.profile_name}' not found\n\nAvailable profiles: {available}\n\n  foundry run {e.available[0] if e.available else 'ci'}", file=sys.stderr)
    else:
        print(f"error: invalid foundry.yaml — {e}", file=sys.stderr)


def cmd_run(args):
    repo = _repo(args)
    profile_name = args.profile or args.profile_flag
    if not profile_name:
        print("error: profile name required\n\n  foundry run ci\n  foundry run --profile ci\n\nRun 'foundry run --help' for options.", file=sys.stderr)
        sys.exit(1)

    debounce_seconds = float(args.debounce.rstrip("s"))

    if args.watch and args.json:
        print("Cannot use --json with --watch", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        try:
            cfg = load_config(str(repo / "foundry.yaml"))
        except FileNotFoundError as e:
            _print_config_error(FileNotFoundError, e, repo)
            sys.exit(1)
        except ValueError as e:
            _print_config_error(ValueError, e, repo)
            sys.exit(1)

        if profile_name not in cfg.profiles:
            _print_config_error(ProfileNotFoundError, ProfileNotFoundError(profile_name, list(cfg.profiles)), repo)
            sys.exit(1)

        profile = cfg.profiles[profile_name]
        from foundry.runner import _resolve_docker, _wrap_docker
        for gate in profile.gates:
            if gate.act:
                info = f"act {gate.act.event} -W {gate.act.workflow}"
            else:
                docker = _resolve_docker(gate, profile.docker)
                if docker:
                    base = ["sh", "-c", gate.run] if gate.run else gate.run.split()
                    info = " ".join(_wrap_docker(base, docker, repo))
                else:
                    info = gate.run
            print(f"  {gate.id}: {info}")
        sys.exit(0)

    if args.watch:
        from foundry.watcher import FoundryWatcher
        watcher = FoundryWatcher(repo=repo, profile=profile_name, debounce=debounce_seconds)
        watcher.start()
        return

    from foundry.global_config import load_global_config
    gcfg = load_global_config()
    repo_alias = next((r["alias"] for r in gcfg.get("repos", []) if r.get("path") == str(repo)), None)
    alias_label = f" (repo: {repo_alias})" if repo_alias else ""
    print(f"[foundry] profile: {profile_name}{alias_label}", file=sys.stderr)

    try:
        result = prepare_and_run_profile(profile_name, repo)
    except ProfileNotFoundError as e:
        _print_config_error(ProfileNotFoundError, e, repo)
        sys.exit(1)
    except FileNotFoundError as e:
        _print_config_error(FileNotFoundError, e, repo)
        sys.exit(1)
    except ValueError as e:
        _print_config_error(ValueError, e, repo)
        sys.exit(1)

    decision = result["decision"]
    print(f"[foundry] decision: {decision}", file=sys.stderr)
    print(f"[foundry] evidence: {repo / '.foundry' / 'runs' / result['run_id']}", file=sys.stderr)

    if args.json:
        print(json.dumps(result, indent=2))

    sys.exit(0 if decision in ("pass", "warn") else 1)


def cmd_latest(args):
    repo = _repo(args)
    latest = repo / ".foundry" / "latest"
    if not latest.exists():
        print("error: no runs found\n\n  foundry run ci\n\nRun a profile first, then use 'foundry latest'.", file=sys.stderr)
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
    from foundry.explain import explain_run, required_env_key
    key = required_env_key(args.model)
    if not os.environ.get(key):
        print(
            f"error: {key} is not set\n\n"
            f"  export {key}=your-api-key\n\n"
            f"Required to use model '{args.model}' with 'foundry explain'.\n"
            f"Use --model claude-opus-4-5 to switch to a Claude model (needs ANTHROPIC_API_KEY).",
            file=sys.stderr,
        )
        sys.exit(1)
    repo = _repo(args)
    run_dir = repo / ".foundry" / "runs" / args.run if args.run else (repo / ".foundry" / "latest").resolve()
    if not run_dir.exists():
        label = args.run if args.run else "latest"
        hint = "Run 'foundry latest' to see available run IDs." if args.run else "Run 'foundry run ci' to create a run first."
        print(f"error: run '{label}' not found at {run_dir}\n\n{hint}", file=sys.stderr)
        sys.exit(1)
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
            print("No repos registered.\n\n  foundry repos add --alias myrepo\n  foundry repos add --alias myrepo --repo /path/to/repo")
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
            aliases = [r.get("alias", r.get("path")) for r in cfg.get("repos", [])]
            hint = f"Known repos: {', '.join(aliases)}" if aliases else "Run 'foundry repos list' to see registered repos."
            print(f"error: no repo matching '{target}'\n\n{hint}", file=sys.stderr)
            sys.exit(1)
        save_global_config(cfg)
        print(f"[foundry] removed repo: {target}", file=sys.stderr)


def cmd_daemon(args):
    from foundry.daemon import daemon_start, daemon_status, daemon_stop, run_daemon_loop
    {"start": daemon_start, "stop": daemon_stop, "status": daemon_status, "run": run_daemon_loop}[args.daemon_cmd]()


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
    fmt = argparse.RawDescriptionHelpFormatter
    repo_parent = argparse.ArgumentParser(add_help=False)
    repo_parent.add_argument("--repo", default=argparse.SUPPRESS, help="Path to repository (default: .)")

    # ponytail: shared parent so --repo works after any subcommand
    parser = argparse.ArgumentParser(
        prog="foundry",
        description="Local quality gate runner",
        formatter_class=fmt,
        epilog=(
            "examples:\n"
            "  foundry init                        # auto-detect gates and write foundry.yaml\n"
            "  foundry run ci                      # run the 'ci' profile\n"
            "  foundry run ci --dry-run            # preview gates without running\n"
            "  foundry run ci --watch              # re-run on file changes\n"
            "  foundry latest                      # show last run summary\n"
            "  foundry latest --json               # print full result.json\n"
            "  foundry explain                     # AI explanation of latest run\n"
            "  foundry schedule install nightly    # install a cron schedule\n"
        ),
    )
    parser.add_argument("--repo", default=".", help="Path to repository (default: .)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", parents=[repo_parent], help="Check environment and tool dependencies",
                   formatter_class=fmt,
                   epilog="examples:\n  foundry doctor\n  foundry doctor --repo /path/to/repo")
    sub.add_parser("init", parents=[repo_parent], help="Generate foundry.yaml from detected gates",
                   formatter_class=fmt,
                   epilog="examples:\n  foundry init\n  foundry init --repo /path/to/repo")

    run_p = sub.add_parser("run", parents=[repo_parent], help="Run a profile",
                           formatter_class=fmt,
                           epilog=(
                               "examples:\n"
                               "  foundry run ci\n"
                               "  foundry run ci --dry-run\n"
                               "  foundry run ci --json\n"
                               "  foundry run ci --watch\n"
                               "  foundry run ci --watch --debounce 5s\n"
                               "  foundry run --profile ci --repo /path/to/repo\n"
                           ))
    run_p.add_argument("profile", nargs="?", default=None, help="Profile name from foundry.yaml")
    run_p.add_argument("--profile", dest="profile_flag", default=None, metavar="NAME",
                       help="Alias for positional profile (for scripting convenience)")
    run_p.add_argument("--json", action="store_true", help="Print result.json to stdout")
    run_p.add_argument("--dry-run", action="store_true", help="Show gates without running")
    run_p.add_argument("--watch", action="store_true", help="Re-run on file changes")
    run_p.add_argument("--debounce", default="2s", metavar="DURATION", help="Debounce interval for --watch (default: 2s)")

    explain_p = sub.add_parser("explain", parents=[repo_parent], help="Explain latest run with AI",
                               formatter_class=fmt,
                               epilog=(
                                   "examples:\n"
                                   "  foundry explain\n"
                                   "  foundry explain --model gemini-3.5-flash\n"
                                   "  foundry explain --model claude-opus-4-5\n"
                                   "  foundry explain --run 2024-01-15T10-30-00Z\n\n"
                                   "env vars:\n"
                                   "  GEMINI_API_KEY      required for gemini-* models\n"
                                   "  ANTHROPIC_API_KEY   required for claude-* models\n"
                               ))
    explain_p.add_argument("--run", default=None, metavar="RUN_ID", help="Specific run ID (default: latest)")
    # gemini-3.5-flash is a real model: https://ai.google.dev/gemini-api/docs/models
    explain_p.add_argument("--model", default="gemini-3.5-flash", metavar="MODEL",
                           help="Model to use: gemini-* or claude-* (default: gemini-3.5-flash)")

    latest_p = sub.add_parser("latest", parents=[repo_parent], help="Show latest run result",
                              formatter_class=fmt,
                              epilog=(
                                  "examples:\n"
                                  "  foundry latest             # one-line summary\n"
                                  "  foundry latest --json      # full result.json\n"
                                  "  foundry latest --summary   # markdown summary\n"
                                  "  foundry latest --evidence  # raw evidence.json\n"
                              ))
    latest_p.add_argument("--json", action="store_true", help="Print full result.json")
    latest_p.add_argument("--summary", action="store_true", help="Print markdown summary")
    latest_p.add_argument("--evidence", action="store_true", help="Print raw evidence.json")

    sched_p = sub.add_parser("schedule", parents=[repo_parent], help="Manage schedules",
                             formatter_class=fmt,
                             epilog=(
                                 "examples:\n"
                                 "  foundry schedule install nightly   # install schedule named 'nightly'\n"
                                 "  foundry schedule list              # list installed schedules\n"
                                 "  foundry schedule remove nightly    # remove a schedule\n"
                             ))
    sched_sub = sched_p.add_subparsers(dest="schedule_cmd")
    install_p = sched_sub.add_parser("install", help="Install a named schedule from foundry.yaml",
                                     formatter_class=fmt,
                                     epilog="example:\n  foundry schedule install nightly")
    install_p.add_argument("name", help="Schedule name defined in foundry.yaml")
    remove_p = sched_sub.add_parser("remove", help="Remove an installed schedule",
                                    formatter_class=fmt,
                                    epilog="example:\n  foundry schedule remove nightly")
    remove_p.add_argument("name", help="Schedule name to remove")
    sched_sub.add_parser("list", help="List installed schedules")

    repos_p = sub.add_parser("repos", parents=[repo_parent], help="Manage tracked repos",
                             formatter_class=fmt,
                             epilog=(
                                 "examples:\n"
                                 "  foundry repos add                          # track current directory\n"
                                 "  foundry repos add --alias myapp            # track with alias\n"
                                 "  foundry repos add --alias myapp --repo /path/to/repo\n"
                                 "  foundry repos list                         # list tracked repos\n"
                                 "  foundry repos remove myapp                 # remove by alias or path\n"
                             ))
    repos_sub = repos_p.add_subparsers(dest="repos_cmd")
    repos_add_p = repos_sub.add_parser("add", help="Track current repo",
                                       formatter_class=fmt,
                                       epilog=(
                                           "examples:\n"
                                           "  foundry repos add\n"
                                           "  foundry repos add --alias myapp\n"
                                           "  foundry repos add --alias myapp --repo /path/to/repo\n"
                                       ))
    repos_add_p.add_argument("--alias", default=None, metavar="NAME", help="Short name for this repo")
    repos_sub.add_parser("list", help="List tracked repos")
    repos_remove_p = repos_sub.add_parser("remove", help="Remove a tracked repo",
                                          formatter_class=fmt,
                                          epilog="example:\n  foundry repos remove myapp")
    repos_remove_p.add_argument("alias_or_path", help="Alias or path to remove")

    daemon_p = sub.add_parser("daemon", help="Auto-run schedules across all registered repos",
                              formatter_class=fmt,
                              epilog=(
                                  "examples:\n"
                                  "  foundry daemon start    # install supervisor + start\n"
                                  "  foundry daemon stop     # stop + remove supervisor\n"
                                  "  foundry daemon status   # show if running\n"
                              ))
    daemon_sub = daemon_p.add_subparsers(dest="daemon_cmd")
    daemon_sub.add_parser("start", help="Install OS supervisor and start daemon")
    daemon_sub.add_parser("stop", help="Stop daemon and remove supervisor")
    daemon_sub.add_parser("status", help="Show daemon status")
    daemon_sub.add_parser("run", help=argparse.SUPPRESS)  # internal: called by supervisor

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {"doctor": cmd_doctor, "init": cmd_init, "run": cmd_run, "latest": cmd_latest, "explain": cmd_explain, "schedule": cmd_schedule, "repos": cmd_repos, "daemon": cmd_daemon}
    if args.command in dispatch:
        if args.command == "schedule" and args.schedule_cmd is None:
            sched_p.print_help()
            sys.exit(0)
        if args.command == "repos" and args.repos_cmd is None:
            repos_p.print_help()
            sys.exit(0)
        if args.command == "daemon" and args.daemon_cmd is None:
            daemon_p.print_help()
            sys.exit(0)
        dispatch[args.command](args)
    else:
        parser.print_help()
        sys.exit(1)
