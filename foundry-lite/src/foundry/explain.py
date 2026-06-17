import anthropic
import json
from pathlib import Path

EXPLAIN_PROMPT = """Gate '{gate_id}' {status} running: {command}

Log output (last 8KB):
{log}

In 5 or fewer concise bullet points, explain what failed and why. Be specific about file paths and line numbers if visible. Do not repeat the log verbatim."""


def _tail(path: Path, nbytes: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(max(0, path.stat().st_size - nbytes))
            return f.read(nbytes).decode(errors="replace")
    except (FileNotFoundError, OSError):
        return ""


def explain_run(run_dir: Path, model: str = "claude-haiku-4-5-20251001") -> str:
    result = json.loads((run_dir / "result.json").read_text())

    if result.get("decision") == "pass":
        return "All gates passed. No explanation needed."

    client = anthropic.Anthropic()
    explanations = []

    for gate in result.get("gates", []):
        status = gate.get("status", "")
        if status not in ("failed", "timed_out"):
            continue

        gate_id = gate.get("id", "unknown")
        command = gate.get("command", "")
        log = _tail(Path(gate["log"]), 8192) if gate.get("log") else ""

        response = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": EXPLAIN_PROMPT.format(
                        gate_id=gate_id, status=status, command=command, log=log
                    ),
                }
            ],
        )

        text = response.content[0].text
        explanations.append(f"## Gate: {gate_id} ({status})\n\n{text}")

    return "\n\n".join(explanations)
