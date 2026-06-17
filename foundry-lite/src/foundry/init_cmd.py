import sys
from pathlib import Path


def detect_gates(repo: Path) -> list[dict]:
    gates = []

    makefile = repo / "Makefile"
    if makefile.exists():
        content = makefile.read_text()
        for target in ("lint", "test", "typecheck", "build"):
            if f"{target}:" in content:
                gates.append({"id": target, "run": f"make {target}"})

    justfile = repo / "justfile"
    if justfile.exists():
        content = justfile.read_text()
        for target in ("lint", "test", "typecheck", "build"):
            if f"{target}:" in content and not any(g["id"] == target for g in gates):
                gates.append({"id": target, "run": f"just {target}"})

    workflows = sorted((repo / ".github" / "workflows").glob("*.yml"))
    if workflows:
        match = workflows[0]
        gates.append({
            "id": "ci",
            "act": {"workflow": str(match.relative_to(repo)), "event": "pull_request"},
            "timeout": "20m",
        })

    return gates


def generate_foundry_yaml(repo: Path) -> str:
    gates = detect_gates(repo)
    non_act = [g for g in gates if "act" not in g]
    act_gates = [g for g in gates if "act" in g]

    def gate_yaml(g, indent=6) -> str:
        pad = " " * indent
        if "act" in g:
            return (
                f"{pad}- id: {g['id']}\n"
                f"{pad}  act:\n"
                f"{pad}    workflow: {g['act']['workflow']}\n"
                f"{pad}    event: {g['act']['event']}\n"
                f"{pad}  timeout: {g['timeout']}\n"
            )
        return f"{pad}- id: {g['id']}\n{pad}  run: {g['run']}\n"

    if not gates:
        return """\
version: 1

profiles:
  quick:
    gates:
      # - id: lint
      #   run: make lint
      # - id: test
      #   run: make test

  full:
    gates:
      # - id: lint
      #   run: make lint
      # - id: test
      #   run: make test
      # - id: ci
      #   act:
      #     workflow: .github/workflows/ci.yml
      #     event: pull_request
      #   timeout: 20m

schedules:
  nightly:
    profile: full
    cron: '0 2 * * *'
"""

    quick_lines = "".join(gate_yaml(g) for g in non_act)
    full_lines = "".join(gate_yaml(g) for g in non_act + act_gates)

    return (
        "version: 1\n\n"
        "profiles:\n"
        "  quick:\n"
        "    gates:\n"
        f"{quick_lines}"
        "  full:\n"
        "    gates:\n"
        f"{full_lines}"
        "schedules:\n"
        "  nightly:\n"
        "    profile: full\n"
        "    cron: '0 2 * * *'\n"
    )


def run_init(repo: Path) -> None:
    dest = repo / "foundry.yaml"
    if dest.exists():
        print("foundry.yaml already exists. Remove it first or edit manually.", file=sys.stderr)
        sys.exit(1)

    gates = detect_gates(repo)
    yaml_text = generate_foundry_yaml(repo)
    dest.write_text(yaml_text)

    # detection summary
    makefile_targets = []
    just_targets = []
    has_ci = False
    for g in gates:
        if "act" in g:
            has_ci = True
        elif g["run"].startswith("make "):
            makefile_targets.append(g["id"])
        elif g["run"].startswith("just "):
            just_targets.append(g["id"])

    if makefile_targets:
        print(f"[foundry] detected: Makefile ({', '.join(makefile_targets)})")
    if just_targets:
        print(f"[foundry] detected: justfile ({', '.join(just_targets)})")
    if has_ci:
        wf = next(g["act"]["workflow"] for g in gates if "act" in g)
        print(f"[foundry] detected: GitHub Actions ({wf})")
    if not gates:
        print("[foundry] no gates detected — placeholder config written")

    non_act = sum(1 for g in gates if "act" not in g)
    full_count = len(gates)
    print(f"[foundry] wrote foundry.yaml ({non_act} gates in quick, {full_count} gates in full)")
