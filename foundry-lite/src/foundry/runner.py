import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from foundry.config import FoundryConfig, GateConfig, ProfileConfig, parse_timeout


LOG_SIZE_CAP = 512 * 1024  # 512KB


@dataclass
class GateResult:
    id: str
    runner: str
    command: str
    status: str
    exit_code: int | None
    started_at: str
    finished_at: str
    duration_ms: int
    log: str
    log_truncated: bool
    allow_failure: bool
    decision_on_failure: str = "fail"


@dataclass
class RunResult:
    run_id: str
    profile: str
    decision: str
    exit_code: int
    started_at: str
    finished_at: str
    repo_path: str
    commit: str
    branch: str
    dirty: bool
    gates: list[GateResult]


def detect_runner(run_cmd: str) -> tuple[str, str]:
    if run_cmd.startswith("make "):
        return "make", run_cmd
    if run_cmd.startswith("just "):
        return "just", run_cmd
    if run_cmd.startswith("task "):
        return "task", run_cmd
    if run_cmd.startswith("mise run "):
        return "mise", run_cmd
    return "shell", f'sh -c "{run_cmd}"'


def _detect_runner(gate: GateConfig) -> tuple[str, str]:
    if gate.act:
        return "act", f"act {gate.act.event} -W {gate.act.workflow}"
    if gate.dagger:
        d = gate.dagger
        cmd = ["dagger", "call", "-m", d.module, d.function] + (d.args or [])
        return "dagger", " ".join(cmd)
    return detect_runner(gate.run)


def get_git_info(repo_path: str) -> dict:
    def _git(*args):
        try:
            return subprocess.run(
                ["git"] + list(args), cwd=repo_path,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        except Exception:
            return ""

    commit = _git("rev-parse", "HEAD") or "unknown"
    branch = _git("branch", "--show-current") or "unknown"
    dirty = bool(_git("status", "--porcelain"))
    return {"commit": commit, "branch": branch, "dirty": dirty}


def run_gate(gate: GateConfig, log_dir: Path, repo_path: Path) -> GateResult:
    if gate.dagger:
        from foundry.dagger_runner import run_dagger_gate
        return run_dagger_gate(gate, log_dir, repo_path)
    gate_id = gate.id
    runner_type, cmd = _detect_runner(gate)
    timeout_secs = parse_timeout(gate.timeout)
    log_path = log_dir / f"{gate_id}.log"

    print(f"[foundry] gate {gate_id} → running ({cmd})", file=sys.stderr)
    started = datetime.now(timezone.utc)
    started_str = started.isoformat()

    # For shell gates, pass original run string to sh -c rather than splitting cmd
    if runner_type == "shell":
        shell_cmd = ["sh", "-c", gate.run]
    else:
        shell_cmd = cmd.split()

    status = "failed"
    exit_code = None
    log_truncated = False
    log_bytes = bytearray()

    def _result(s, ec, fin):
        return GateResult(
            id=gate_id, runner=runner_type, command=cmd, status=s,
            exit_code=ec, started_at=started_str, finished_at=fin.isoformat(),
            duration_ms=int((fin - started).total_seconds() * 1000),
            log=str(log_path), log_truncated=log_truncated,
            allow_failure=gate.allow_failure, decision_on_failure=gate.decision_on_failure,
        )

    try:
        proc = subprocess.Popen(
            shell_cmd,
            cwd=str(repo_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        def _reader():
            nonlocal log_truncated
            for chunk in iter(lambda: proc.stdout.read(4096), b""):
                sys.stderr.buffer.write(chunk)
                sys.stderr.buffer.flush()
                remaining = LOG_SIZE_CAP - len(log_bytes)
                if remaining > 0:
                    log_bytes.extend(chunk[:remaining])
                    if len(log_bytes) >= LOG_SIZE_CAP:
                        log_truncated = True

        t = threading.Thread(target=_reader, daemon=True)
        t.start()

        try:
            proc.wait(timeout=timeout_secs)
            t.join()
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            t.join(timeout=2)
            log_path.write_bytes(bytes(log_bytes))
            finished = datetime.now(timezone.utc)
            print(f"[foundry] gate {gate_id} → timed_out ({gate.timeout})", file=sys.stderr)
            return _result("timed_out", None, finished)

        exit_code = proc.returncode
        log_path.write_bytes(bytes(log_bytes))

        log_content = log_bytes.decode(errors="replace")
        if "FOUNDRY_NEEDS_HUMAN" in log_content:
            status = "needs_human"
        elif exit_code == 0:
            status = "passed"

    except FileNotFoundError:
        exit_code = 127

    finished = datetime.now(timezone.utc)
    print(f"[foundry] gate {gate_id} → {status} ({int((finished - started).total_seconds())}s)", file=sys.stderr)
    return _result(status, exit_code, finished)


def derive_decision(gate_results: list[GateResult]) -> str:
    if any(r.status == "needs_human" for r in gate_results):
        return "needs_human"
    if any(r.status in ("failed", "timed_out") and not r.allow_failure for r in gate_results):
        return "fail"
    if any(
        r.status in ("failed", "timed_out")
        and r.allow_failure
        and r.decision_on_failure == "warn"
        for r in gate_results
    ):
        return "warn"
    return "pass"


def run_profile_parallel(profile: ProfileConfig, run_dir: Path, repo_path: Path) -> list[GateResult]:
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=len(profile.gates)) as pool:
        futures = {pool.submit(run_gate, gate, log_dir, repo_path): gate for gate in profile.gates}
        return [f.result() for f in futures]


def run_profile(
    profile: ProfileConfig,
    profile_name: str,
    run_dir: Path,
    repo_path: Path,
    *,
    integrations_cfg: dict | None = None,
    run_id: str | None = None,
    git_info: dict | None = None,
    started_at: str | None = None,
) -> dict:
    if profile.parallel:
        gate_results = run_profile_parallel(profile, run_dir, repo_path)
    else:
        log_dir = run_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        gates = profile.gates
        gate_results = []

        for i, gate in enumerate(gates):
            result = run_gate(gate, log_dir, repo_path)
            gate_results.append(result)
            # ponytail: fail-fast on required gate failure
            if result.status in ("failed", "timed_out") and not result.allow_failure:
                now = datetime.now(timezone.utc).isoformat()
                for skipped in gates[i + 1:]:
                    runner_type, cmd = _detect_runner(skipped)
                    gate_results.append(GateResult(
                        id=skipped.id, runner=runner_type, command=cmd, status="skipped",
                        exit_code=None, started_at=now, finished_at=now, duration_ms=0,
                        log="", log_truncated=False, allow_failure=skipped.allow_failure,
                        decision_on_failure=skipped.decision_on_failure,
                    ))
                break

    finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    decision = derive_decision(gate_results)
    exit_code = 0 if decision in ("pass", "warn") else 1
    gi = git_info or {}
    run_result_obj = RunResult(
        run_id=run_id or "unknown",
        profile=profile_name,
        decision=decision,
        exit_code=exit_code,
        started_at=started_at or finished_at,
        finished_at=finished_at,
        repo_path=str(repo_path),
        commit=gi.get("commit", "unknown"),
        branch=gi.get("branch", "unknown"),
        dirty=gi.get("dirty", False),
        gates=gate_results,
    )
    from foundry.output import write_evidence, write_result_json, write_summary_md
    result_dict = write_result_json(run_result_obj, run_dir)
    write_summary_md(run_result_obj, run_dir)
    write_evidence(run_dir, result_dict)
    _fire_integrations(integrations_cfg or {}, result_dict, run_dir, repo_path)
    return result_dict


def _fire_integrations(integrations_cfg: dict, result: dict, run_dir: Path, repo_path: Path) -> None:
    decision = result["decision"]
    is_failure = decision in ("fail", "needs_human")
    if not is_failure:
        return

    explanation: str | None = None

    explain_cfg = integrations_cfg.get("explain", {})
    if explain_cfg and explain_cfg.get("on_failure"):
        import os
        from foundry.explain import explain_run, required_env_key
        model = explain_cfg.get("model", "gemini-3.5-flash")
        if os.environ.get(required_env_key(model)):
            try:
                explanation = explain_run(run_dir, model)
                (run_dir / "explanation.md").write_text(explanation)
                print(explanation, file=sys.stderr)
            except Exception as e:
                print(f"[foundry] explain failed: {e}", file=sys.stderr)
        else:
            print(f"[foundry] explain skipped: {required_env_key(model)} not set", file=sys.stderr)

    maybe_create_beads_issue(integrations_cfg, result, run_dir, explanation)

    agent_cfg = integrations_cfg.get("agent", {})
    if agent_cfg and agent_cfg.get("on_failure"):
        cmd_template = agent_cfg.get("command", "claude --print 'Foundry run failed. See {run_dir}/result.json'")
        cmd = cmd_template.format(run_dir=run_dir)
        try:
            subprocess.Popen(["sh", "-c", cmd], cwd=str(repo_path), start_new_session=True)
            print(f"[foundry] agent spawned: {cmd}", file=sys.stderr)
        except Exception as e:
            print(f"[foundry] agent spawn failed: {e}", file=sys.stderr)


def maybe_create_beads_issue(integrations_cfg: dict, result: dict, run_dir: Path, explanation: str | None = None) -> None:
    beads_cfg = integrations_cfg.get("beads", {})
    if not beads_cfg:
        return

    decision = result["decision"]
    if not (
        (decision == "fail" and beads_cfg.get("on_failure")) or
        (decision == "needs_human" and beads_cfg.get("on_needs_human"))
    ):
        return

    try:
        summary = (run_dir / "summary.md").read_text()[:2048]
    except FileNotFoundError:
        summary = f"foundry run {result['run_id']} {decision}"

    description = f"{summary}\n\n## AI Explanation\n\n{explanation}" if explanation else summary
    description = description[:4096]

    commit = result["repo"]["commit"]
    title = f"foundry: {result['profile']} {decision} @ {commit[:7]}"

    try:
        proc = subprocess.run(
            ["bd", "create", "--title", title, "--description", description, "--type", "bug", "--priority", "2"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        print("[foundry] warning: bd not found in PATH, skipping beads issue creation", file=sys.stderr)
        return

    if proc.returncode != 0:
        print(f"[foundry] warning: bd create failed: {proc.stderr.strip()}", file=sys.stderr)
        return

    issue_id = proc.stdout.strip().split()[-1]
    print(f"[foundry] beads issue created: {issue_id}", file=sys.stderr)
    result["beads_issue_id"] = issue_id
