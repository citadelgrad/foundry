import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from foundry.config import GateConfig, parse_timeout
from foundry.runner import GateResult, LOG_SIZE_CAP


def run_dagger_gate(gate: GateConfig, log_dir: Path, repo_path: Path) -> GateResult:
    gate_id = gate.id
    d = gate.dagger
    cmd = ["dagger", "call", "-m", d.module, d.function] + (d.args or [])
    cmd_str = " ".join(cmd)
    timeout_secs = parse_timeout(gate.timeout)
    log_path = log_dir / f"{gate_id}.log"

    print(f"[foundry] gate {gate_id} → running ({cmd_str})", file=sys.stderr)
    started = datetime.now(timezone.utc)
    started_str = started.isoformat()

    status = "failed"
    exit_code = None
    log_truncated = False
    log_bytes = bytearray()

    def _result(s, ec, fin):
        return GateResult(
            id=gate_id, runner="dagger", command=cmd_str, status=s,
            exit_code=ec, started_at=started_str, finished_at=fin.isoformat(),
            duration_ms=int((fin - started).total_seconds() * 1000),
            log=str(log_path), log_truncated=log_truncated,
            allow_failure=gate.allow_failure, decision_on_failure=gate.decision_on_failure,
        )

    try:
        proc = subprocess.Popen(
            cmd,
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
