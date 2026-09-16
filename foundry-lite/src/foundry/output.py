import hashlib
import json
import os
import platform
import sys
from pathlib import Path

from foundry.runner import RunResult


def write_result_json(run_result: RunResult, run_dir: Path) -> dict:
    result = {
        "schema_version": 1,
        "run_id": run_result.run_id,
        "profile": run_result.profile,
        "decision": run_result.decision,
        "exit_code": run_result.exit_code,
        "started_at": run_result.started_at,
        "finished_at": run_result.finished_at,
        "repo": {
            "path": run_result.repo_path,
            "commit": run_result.commit,
            "branch": run_result.branch,
            "dirty": run_result.dirty,
        },
        "gates": [
            {
                "id": g.id,
                "runner": g.runner,
                "command": g.command,
                "status": g.status,
                "exit_code": g.exit_code,
                "started_at": g.started_at,
                "finished_at": g.finished_at,
                "duration_ms": g.duration_ms,
                "log": g.log,
                "log_truncated": g.log_truncated,
                "allow_failure": g.allow_failure,
            }
            for g in run_result.gates
        ],
        "agent_recommendation": {
            "safe_to_continue": run_result.decision in ("pass", "warn"),
            "safe_to_commit": run_result.decision == "pass",
            "needs_human": run_result.decision == "needs_human",
            "summary": derive_summary_text(run_result),
        },
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2))
    return result


def write_evidence(run_dir: Path, result: dict) -> None:
    result_bytes = json.dumps(result, sort_keys=True).encode()
    result_hash = "sha256:" + hashlib.sha256(result_bytes).hexdigest()
    evidence = {
        "schema_version": 1,
        "run_id": result["run_id"],
        "decision": result["decision"],
        "profile": result["profile"],
        "repo_commit": result["repo"]["commit"],
        "result_hash": result_hash,
        "foundry_version": result.get("foundry_version", "unknown"),
        "issued_at": result["finished_at"],
    }
    (run_dir / "evidence.json").write_text(json.dumps(evidence, indent=2))


def derive_summary_text(run_result: RunResult) -> str:
    d = run_result.decision
    if d == "pass":
        return f"All {run_result.profile} gates passed."
    if d == "warn":
        return "Required gates passed; some optional gates failed."
    if d == "fail":
        return "One or more required gates failed."
    return "A gate requires human review before continuing."


def write_summary_md(run_result: RunResult, run_dir: Path) -> None:
    total_ms = sum(g.duration_ms for g in run_result.gates)
    total_s = total_ms // 1000
    duration = f"{total_s // 60}m {total_s % 60}s" if total_s >= 60 else f"{total_s}s"

    icons = {"passed": "✅", "failed": "❌", "timed_out": "⏱️", "skipped": "⏭️", "needs_human": "🤔"}
    rows = "\n".join(
        f"| {g.id} | {icons.get(g.status, g.status)} {g.status} | {g.duration_ms // 1000}s |"
        for g in run_result.gates
    )

    rec = run_result.decision
    safe_continue = "yes" if rec in ("pass", "warn") else "no"
    safe_commit = "yes" if rec == "pass" else "no"
    needs_human = "yes" if rec == "needs_human" else "no"

    md = f"""# Foundry Run: {run_result.profile}

**Decision:** {run_result.decision}
**Profile:** {run_result.profile}
**Commit:** {run_result.commit} ({run_result.branch})
**Duration:** {duration}
**Run ID:** {run_result.run_id}

## Gates

| Gate | Status | Duration |
|------|--------|----------|
{rows}

## Agent Recommendation

Safe to continue: **{safe_continue}**
Safe to commit: **{safe_commit}**
Needs human: {needs_human}

> {derive_summary_text(run_result)}
"""
    (run_dir / "summary.md").write_text(md)


def write_metadata_json(run_dir: Path, foundry_version: str = "0.1.0") -> None:
    (run_dir / "metadata.json").write_text(json.dumps({
        "foundry_version": foundry_version,
        "python_version": sys.version.split()[0],
        "platform": platform.system().lower(),
        "log_size_cap_bytes": 524288,
    }, indent=2))


def update_latest_symlink(run_dir: Path) -> None:
    latest = run_dir.parent.parent / "latest"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    os.symlink(str(run_dir), str(latest))


def write_gitignore_if_missing(foundry_dir: Path) -> None:
    gitignore = foundry_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# Foundry run artifacts\nruns/\n")
