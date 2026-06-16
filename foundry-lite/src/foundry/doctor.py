import shutil
import subprocess
from pathlib import Path

from foundry.config import load_config


def run_doctor(repo_path: Path) -> int:
    checks = []

    # 1. git repo check
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        capture_output=True, text=True, cwd=repo_path,
    )
    if result.returncode == 0:
        checks.append(("PASS", f"git: repo detected at {result.stdout.strip()}"))
    else:
        checks.append(("FAIL", "git: not a git repository"))

    # 2. foundry.yaml check
    cfg = None
    try:
        cfg = load_config(str(repo_path / "foundry.yaml"))
        checks.append(("PASS", "foundry.yaml: valid"))
    except FileNotFoundError:
        checks.append(("FAIL", "foundry.yaml: not found"))
        _print_checks(checks)
        return 1
    except ValueError as e:
        checks.append(("FAIL", f"foundry.yaml: parse error: {e}"))
        _print_checks(checks)
        return 1

    all_gates = [gate for profile in cfg.profiles.values() for gate in profile.gates]
    has_act_gates = any(gate.act is not None for gate in all_gates)

    # 3. Docker check (act gates only)
    if has_act_gates:
        r = subprocess.run(["docker", "info"], capture_output=True)
        if r.returncode == 0:
            checks.append(("PASS", "docker: daemon running"))
        else:
            checks.append(("FAIL", "docker: daemon not running (required for Act gates)"))

    # 4. Act binary check (act gates only)
    if has_act_gates:
        act_path = shutil.which("act")
        if act_path:
            checks.append(("PASS", f"act: found at {act_path}"))
        else:
            checks.append(("FAIL", "act: not found on PATH (required for Act gates)"))

    # 5. Runner check — per gate command prefix
    run_gates = [gate for gate in all_gates if gate.run]
    if run_gates:
        if any(g.run.startswith("make") for g in run_gates):
            if (repo_path / "Makefile").exists():
                checks.append(("PASS", "runners: Makefile detected"))
            else:
                checks.append(("WARN", "runners: Makefile not found (make gates may fail)"))
        if any(g.run.startswith("just") for g in run_gates):
            if (repo_path / "justfile").exists() or (repo_path / "Justfile").exists():
                checks.append(("PASS", "runners: justfile detected"))
            else:
                checks.append(("WARN", "runners: justfile not found (just gates may fail)"))
        if any(g.run.startswith(("task", "mise")) for g in run_gates):
            if (repo_path / "Taskfile.yml").exists() or (repo_path / "Taskfile.yaml").exists():
                checks.append(("PASS", "runners: Taskfile detected"))
            else:
                checks.append(("WARN", "runners: Taskfile not found (task gates may fail)"))

    # 6. Workflows check (act gates only)
    if has_act_gates:
        wf_dir = repo_path / ".github" / "workflows"
        wf_files = list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml")) if wf_dir.exists() else []
        if wf_files:
            checks.append(("PASS", f"workflows: found {len(wf_files)} workflow files"))
        else:
            checks.append(("WARN", "workflows: no workflow files found (Act gates will fail)"))

    _print_checks(checks)
    return 1 if any(level == "FAIL" for level, _ in checks) else 0


def _print_checks(checks: list[tuple[str, str]]) -> None:
    for level, msg in checks:
        print(f"[{level}] {msg}")
