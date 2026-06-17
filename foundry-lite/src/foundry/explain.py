import json
import os
from pathlib import Path

EXPLAIN_PROMPT = """Gate '{gate_id}' {status} running: {command}

Log output (last 8KB):
{log}

In 5 or fewer concise bullet points, explain what failed and why. Be specific about file paths and line numbers if visible. Do not repeat the log verbatim."""


def _tail(path: Path, nbytes: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - nbytes))
            return f.read(nbytes).decode(errors="replace")
    except (FileNotFoundError, OSError):
        return ""


def _call_model(model: str, prompt: str) -> str:
    if model.startswith("gemini-"):
        from google import genai
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        return client.models.generate_content(model=model, contents=prompt).text
    else:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text


def required_env_key(model: str) -> str:
    return "GOOGLE_API_KEY" if model.startswith("gemini-") else "ANTHROPIC_API_KEY"


# gemini-3.5-flash is a real model: https://ai.google.dev/gemini-api/docs/models
def explain_run(run_dir: Path, model: str = "gemini-3.5-flash") -> str:
    result = json.loads((run_dir / "result.json").read_text())

    if result.get("decision") == "pass":
        return "All gates passed. No explanation needed."

    explanations = []
    for gate in result.get("gates", []):
        status = gate.get("status", "")
        if status not in ("failed", "timed_out"):
            continue

        gate_id = gate.get("id", "unknown")
        command = gate.get("command", "")
        log = _tail(Path(gate["log"]), 8192) if gate.get("log") else ""
        prompt = EXPLAIN_PROMPT.format(gate_id=gate_id, status=status, command=command, log=log)

        text = _call_model(model, prompt)
        explanations.append(f"## Gate: {gate_id} ({status})\n\n{text}")

    return "\n\n".join(explanations) if explanations else "No failed or timed-out gates to explain."
