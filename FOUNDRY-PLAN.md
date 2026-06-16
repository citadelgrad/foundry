# Foundry Plan

## One-line definition

Foundry is a local quality evidence runner for AI-assisted development. It runs repo-defined gates locally and on a schedule, captures logs/artifacts, and emits a clear decision that humans, agents, PAS, and Reckoner can trust.

## Non-goals

Foundry is not:

- a new CI system
- a GitHub Actions replacement
- a Dagger competitor
- an n8n replacement
- a repo initializer
- a PAS/Reckoner orchestrator
- a dashboard-first product

## Core idea

Use existing execution tools. Add a stable evidence contract around them.

- Act runs GitHub Actions locally in Docker/Colima.
- Make/just/Task/mise expose repo-local commands.
- Foundry chooses the gates, runs them, captures evidence, classifies failures, and returns a decision.

## MVP scope

Build the boring slice first:

1. `foundry doctor`
   - validate git
   - validate Docker/Colima
   - validate Act
   - validate repo config
   - detect Makefile / justfile / Taskfile / mise tasks
   - detect `.github/workflows`

2. `foundry run <profile>`
   - read `foundry.yaml`
   - run configured gates
   - support shell/Make/just/Task/mise commands
   - support Act-backed GitHub Actions runs
   - write logs and artifacts
   - emit exit code

3. Output contract
   - `.foundry/runs/<run-id>/result.json`
   - `.foundry/runs/<run-id>/summary.md`
   - `.foundry/runs/<run-id>/logs/*.log`
   - `.foundry/runs/<run-id>/artifacts/`

4. Decisions
   - `continue`
   - `retry`
   - `stop`
   - `warn`
   - `needs_human`

5. Scheduling
   - no daemon at first
   - install cron/launchd entries that call `foundry run <profile>`
   - scheduled runs emit the same evidence format

## Suggested commands

```bash
foundry doctor
foundry run quick
foundry run full
foundry run gha --json
foundry latest --summary
foundry explain --latest
foundry schedule install nightly
foundry schedule list
foundry schedule remove nightly
```

## Suggested config

```yaml
version: 1

profiles:
  quick:
    gates:
      - id: lint
        run: make lint
      - id: test
        run: make test

  gha:
    gates:
      - id: github-actions
        act:
          workflow: .github/workflows/ci.yml
          event: pull_request

  full:
    gates:
      - id: actionlint
        run: actionlint
      - id: local-ci
        act:
          workflow: .github/workflows/ci.yml
          event: pull_request
      - id: mutation
        run: make mutation
        allow_failure: true
        decision_on_failure: warn

schedules:
  nightly:
    profile: full
    cron: "0 2 * * *"
```

## Suggested result schema

```json
{
  "schema_version": 1,
  "run_id": "2026-06-15T12-00-00Z",
  "profile": "quick",
  "decision": "continue",
  "status": "passed",
  "exit_code": 0,
  "started_at": "2026-06-15T12:00:00Z",
  "finished_at": "2026-06-15T12:03:14Z",
  "gates": [
    {
      "id": "lint",
      "runner": "shell",
      "command": "make lint",
      "status": "passed",
      "exit_code": 0,
      "log": "logs/lint.log"
    }
  ],
  "agent_recommendation": {
    "safe_to_continue": true,
    "safe_to_commit": true,
    "needs_human": false,
    "summary": "All quick gates passed."
  }
}
```

## Runner backends

Start with:

- shell
- make
- just
- task
- mise
- act

Later:

- dagger
- mutmut
- stryker
- semgrep
- gitleaks
- trivy
- fresh-eyes review agent

## Dagger position

Dagger is not a bad option. It is just not the MVP default.

Act is compatibility-first: it runs existing GitHub Actions locally.

Dagger is abstraction-first: it defines portable programmable pipelines.

Foundry should start Act-first because that keeps existing GitHub Actions as the CI contract. Dagger can become a runner backend later for repos that need stronger container-native pipeline composition.

## Act isolation model

Act itself is a local CLI binary. It uses Docker/Colima to run GitHub Actions jobs in containers.

Foundry MVP should not run Act inside another container. That adds Docker socket / Docker-in-Docker complexity without enough benefit.

Preferred MVP stack:

```text
host:
  foundry CLI
  act CLI
  docker/colima:
    GitHub Actions job containers
```

Optional later isolation:

- temporary git worktrees per run
- pinned Act runner images
- containerized Foundry for packaging
- VM/microVM isolation for high-risk runs

## Open research conclusion

Existing tools solve execution, task running, CI, workflow automation, or build graphing.

No tool found fully solves:

> local repo-defined quality gates + Act/GitHub Actions compatibility + scheduled profiles + artifact/log capture + structured agent-readable decision output.

That is the Foundry niche.

## Best first experiment

Build `foundry-lite`:

1. Read a minimal `foundry.yaml`.
2. Run shell/Make gates.
3. Run Act when configured.
4. Write logs.
5. Emit `result.json` and `summary.md`.
6. Return a decision.
7. Schedule it with launchd/cron.
8. Try it on one real repo before expanding scope.
