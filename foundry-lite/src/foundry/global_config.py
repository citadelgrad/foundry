import sys
from pathlib import Path

import yaml

CONFIG_PATH = Path.home() / ".config" / "foundry" / "config.yaml"


def load_global_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"version": 1, "repos": []}
    return yaml.safe_load(CONFIG_PATH.read_text()) or {"version": 1, "repos": []}


def save_global_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(yaml.dump(cfg, default_flow_style=False))
    except PermissionError as e:
        print(f"error: cannot write {CONFIG_PATH}: {e}", file=sys.stderr)
        sys.exit(1)


def add_repo(path: Path, alias: str | None = None) -> None:
    cfg = load_global_config()
    alias = alias or path.name
    cfg["repos"] = [r for r in cfg.get("repos", []) if r.get("path") != str(path)]
    cfg["repos"].append({"path": str(path), "alias": alias})
    save_global_config(cfg)


def list_repos() -> list[dict]:
    cfg = load_global_config()
    rows = []
    for r in cfg.get("repos", []):
        repo_path = Path(r["path"])
        runs_dir = repo_path / ".foundry" / "runs"
        last_run = None
        last_decision = None
        if runs_dir.exists():
            run_dirs = sorted(runs_dir.glob("*/"), reverse=True)
            if run_dirs:
                result_file = run_dirs[0] / "result.json"
                if result_file.exists():
                    import json
                    data = json.loads(result_file.read_text())
                    last_run = run_dirs[0].name[:10]  # date portion of run_id
                    last_decision = data.get("decision")
        rows.append({
            "alias": r.get("alias", repo_path.name),
            "path": r["path"],
            "last_run": last_run,
            "last_decision": last_decision,
        })
    return rows
