# Foundry Notes — Local Gates for AI Development

Date: 2026-06-15

## Working definition

Foundry is a local quality runner for AI-assisted development: it runs repo-defined gates locally and on a schedule, captures evidence, and returns a clear decision that humans, agents, PAS, and Reckoner can trust.

The important constraint: Foundry should not become another CI system, another workflow builder, or another orchestrator. It should be a verification/evidence primitive.

## Original need

The starting idea was small:

- run pipelines locally
- schedule them
- avoid expensive cloud CI for routine checks
- catch issues earlier in the development process
- run in containers managed by local/IaC-style configuration
- support heavier checks such as mutation testing, fresh-eyes review, security review, and full pipeline verification

That original need is still the core. Everything else should be treated as future integration surface, not MVP scope.

## Act discovery

`nektos/act` already exists and is mature. It runs GitHub Actions locally in Docker.

Implication: Foundry should be Act-first. It should not clone GitHub Actions or invent a competing CI runner.

Act should handle:

- running `.github/workflows/*.yml` locally
- Docker-backed job execution
- GitHub Actions event simulation
- reuse of existing CI definitions

Foundry should add:

- repo baseline enforcement from `.init`
- Makefile target fallback where workflows do not exist
- agent-readable failure summaries
- structured logs/results/artifacts
- scheduling profiles
- stop/retry/continue decisions
- optional integration with PAS, Reckoner, Beads, and LLM gateway

## Why not just n8n?

n8n is a workflow automation canvas. It is good for scheduling, calling APIs, webhooks, notifications, and human-friendly business automation.

Foundry is only justified if the problem is not merely “run some steps on a schedule,” but rather:

> local, cheap, repeatable verification for AI-built code, wired into repo conventions, Act/GitHub Actions, PAS/Reckoner, and agent decision-making.

Use n8n if the immediate need is orchestration. Do not put quality-gate logic inside n8n.

Good n8n use:

```bash
foundry run --repo /repos/foo --profile nightly --json
```

Bad n8n use:

- parse project type in UI nodes
- manually branch between Python/TS/Rust checks
- grep logs in workflow nodes
- decide agent retry/continue logic inside n8n

That becomes low-code Jenkins.

## Four-boundary model

The four-boundary model is useful as an architecture map, not MVP scope.

### 1. Project baseline boundary

Includes:

- `scott-cc .init`
- Makefile targets
- hooks
- `AGENTS.md` / `CLAUDE.md`
- Beads
- local skills and slash commands

Why Foundry might touch it:

- every repo needs a quality contract
- agents should not guess whether to run `npm test`, `uv run pytest`, `cargo test`, etc.
- `.init` can generate `foundry.yaml`

Boundary decision:

- scott-cc owns initialization
- Foundry consumes and validates the contract
- Foundry should not become a repo initializer

Good: “Foundry reads the project contract.”
Bad: “Foundry initializes your whole repo.”

### 2. Local execution boundary

This is the core MVP.

Includes:

- Makefile targets
- Act workflows
- local/container execution
- scheduling
- logs/artifacts
- reports

Why Foundry belongs here:

- this is the original problem
- local execution should be cheap, repeatable, and repo-defined
- routine validation should not require cloud spend

Risks:

- local environment drift
- Act is close to GitHub Actions, not identical
- container execution can become annoying across languages/repos
- scheduling adds daemon/process complexity

Boundary decision:

Foundry owns this.

### 3. Agent pipeline boundary

Includes:

- PAS
- Reckoner
- pipeline stages
- PR workflows
- agent retry/continue behavior

Why Foundry might touch it:

- PAS/Reckoner need a gate between steps
- after an agent changes code, something should decide whether to continue
- agents should consume structured failures, not scrollback

Boundary decision:

- phase 2, not MVP
- Foundry should be CLI-first
- PAS/Reckoner should call `foundry run --json`
- Foundry should not know too much about Reckoner internals

Good: `reckoner verify` shells out to Foundry.
Bad: Foundry becomes a competing orchestrator.

### 4. Safety and memory boundary

Includes:

- Beads
- LLM governance gateway
- security checks
- policy evidence
- audit logs

Why Foundry might touch it:

- security/policy failures should be first-class
- failure follow-up should become durable work
- gateway output can be part of the evidence bundle

Boundary decision:

- phase 3, optional
- Foundry should emit structured results first
- Beads/gateway integrations can consume/augment those results later

Good: Foundry writes `result.json`; other tools consume it.
Bad: Foundry becomes Beads + gateway + security platform.

## Clean product boundary

Foundry owns:

- local execution
- scheduling
- evidence capture
- result summarization
- pass/fail/retry/continue decision

Foundry does not own:

- repo initialization
- agent implementation
- issue tracking
- LLM policy enforcement
- production deployment
- PR creation
- pipeline graph orchestration

Foundry integrates with those, but should not absorb them.

## MVP recommendation

Build A, design toward B.

A. Foundry as local pipeline runner/scheduler:

- read `foundry.yaml`
- run repo-defined commands
- run Act workflow locally
- run scheduled profiles
- store logs/artifacts
- emit `result.json`, `summary.md`, exit code
- support `--json` for other tools

B. Future Foundry as AI development evidence layer:

- PAS/Reckoner gate node
- Beads follow-up creation
- gateway policy evidence
- heavy-gate profiles
- production verification handoff

Do not start with B.

## Suggested command shape

```bash
foundry doctor
foundry run
foundry run --profile fast
foundry run --profile nightly
foundry run --act pull_request
foundry schedule add nightly
foundry report --latest
foundry explain --latest
```

## Suggested `foundry.yaml`

```yaml
version: 1

runner:
  mode: host # later: container
  timeout: 30m

commands:
  setup: make setup
  format: make format-check
  lint: make lint
  typecheck: make typecheck
  test: make test
  build: make build

act:
  enabled: true
  workflow: .github/workflows/ci.yml
  event: pull_request

profiles:
  fast:
    stages: [lint, typecheck, test]
  full:
    stages: [format, lint, typecheck, test, build, act]
  nightly:
    stages: [full, mutation, fresh-eyes, security]

artifacts:
  dir: .foundry/runs

policy:
  require_clean_git: false
  fail_on_secret_scan: true
  max_log_bytes: 200000
```

## Result schema sketch

```json
{
  "version": 1,
  "repo": "your-org/your-repo",
  "commit": "abc123",
  "profile": "full",
  "decision": "continue",
  "status": "passed",
  "started_at": "...",
  "finished_at": "...",
  "project": {
    "contract_valid": true,
    "make_targets_found": ["lint", "typecheck", "test", "build"]
  },
  "local_execution": {
    "status": "passed",
    "stages": []
  },
  "act": {
    "status": "passed",
    "workflow": ".github/workflows/ci.yml",
    "event": "pull_request"
  },
  "agent_pipeline": {
    "decision": "continue",
    "reason": "required gates passed"
  },
  "safety": {
    "secret_scan": "passed",
    "policy": "not_configured"
  },
  "artifacts": {
    "summary_md": ".foundry/runs/.../summary.md",
    "logs_dir": ".foundry/runs/.../logs"
  }
}
```

## Key risks

- building a control plane before proving the runner
- duplicating Act
- hiding critical quality logic inside n8n
- letting `foundry.yaml` become GitHub Actions Lite
- coupling too tightly to PAS/Reckoner
- making local green look like production proof
- creating another orchestrator instead of a gate primitive

## Best next experiment

Create a tiny `foundry-lite` prototype:

1. Read a minimal config.
2. Run Makefile targets.
3. Run Act if workflow exists.
4. Write logs.
5. Emit JSON + Markdown summary.
6. Return a decision.
7. Run it from cron or n8n for one repo.
8. Let PAS/Reckoner call it manually.

Only graduate it into a real Rust CLI after the workflow proves useful.
