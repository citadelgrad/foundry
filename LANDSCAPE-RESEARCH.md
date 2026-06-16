# Foundry Landscape Research

Date: 2026-06-15

Question: does an existing tool solve the proposed Foundry niche: a local quality runner/scheduler/evidence layer for AI-assisted development that runs repo-defined gates locally, integrates with Act/GitHub Actions, Makefile targets and agent pipelines, stores artifacts/logs, and emits structured agent-readable decisions?

## Short answer

No single tool found provides the combined contract.

The nearest primitives are:

- `act` for local GitHub Actions execution
- Make / Task / just / mise tasks for repo-defined command normalization
- Dagger for programmable local-first pipelines and observability
- n8n / Temporal / cron for scheduling and general orchestration
- Buildkite / Woodpecker / Drone / Jenkins for full CI platforms
- Bazel / Pants / Nx / Turborepo for build/test graph execution
- mutmut / Stryker / Semgrep / Gitleaks / Trivy-style tools for specific heavy gates

The recommendation is to build Foundry as a thin orchestrator/evidence layer around existing tools, especially Act and repo-defined commands, not as a new CI/build engine.

## Foundry's target contract

A credible replacement would need most of these:

1. Repo-local config, e.g. `foundry.yaml`.
2. Runs local commands: Makefile, Taskfile, just, mise tasks, arbitrary shell.
3. Runs GitHub Actions locally through `act`.
4. Supports profiles/schedules: `quick`, `precommit`, `full`, `nightly`, `security`.
5. Captures logs/artifacts in a predictable local directory.
6. Emits stable machine-readable `result.json`.
7. Emits human-readable `summary.md`.
8. Returns meaningful exit code.
9. Produces explicit agent decision: `pass`, `fail`, `warn`, `blocked`, `skipped`, `needs_human`.
10. Is designed for AI-assisted development loops, not only humans or CI.

No researched tool clearly provides that combined contract.

## Tool landscape

| Tool | Overlap with Foundry | Gaps vs Foundry | Recommendation |
|---|---|---|---|
| `nektos/act` | Very high for local GitHub Actions execution. | Not scheduler, evidence store, decision layer, or Make/task normalizer. | Use as core dependency. Foundry should be Act-first. |
| `actionlint` | Static checking for GitHub Actions workflows. | Does not run workflows or coordinate decisions. | Include as built-in gate option. |
| Make | Ubiquitous repo-defined command interface. | No structured evidence, scheduling, artifacts, agent API. | Support directly. |
| Taskfile / Task | Cross-platform YAML task runner. | Not GHA runner, scheduler, evidence layer, or decision API. | Support as task backend. |
| just | Simple local command runner. | No logs/artifacts/decision model/scheduler. | Support as task backend. |
| mise tasks | Tool version manager plus task system. | Not an evidence-producing quality orchestrator. | Support optionally. Useful for env/toolchain setup. |
| Dagger | Strong local-first programmable pipeline overlap. | Not GitHub-Actions-native; requires Dagger modules/SDK; lacks simple Foundry-style decision contract. | Closest alternative. Consider for advanced execution, but not MVP dependency. |
| Earthly | Repeatable local/CI builds. | No longer actively maintained per upstream messaging; not Act-first. | Avoid as foundation. |
| Bazel | Powerful build/test execution and caching. | Heavy build-system adoption; not Act/Make wrapper or agent evidence API. | Integrate if repo already uses it; do not adopt for Foundry. |
| Pants | Strong monorepo build/test/lint orchestration. | Requires Pants modeling; not Act-first. | Integrate if present. |
| Nx | Smart JS/monorepo task graph and caching. | JS/monorepo-centric; no Act/agent decision layer. | Backend target for JS monorepos. |
| Turborepo | Fast JS/TS task execution/caching. | Narrower scope; no scheduler/evidence/Act integration by default. | Use as gate command if present. |
| Garden / Tilt / Skaffold / DevSpace | Strong local Kubernetes/dev-loop tooling. | Kubernetes/dev-environment focused, not general repo quality gates. | Not replacement. |
| n8n | Scheduling, webhooks, notifications, workflow automation. | Not repo-native quality runner, not Act-first, not artifact/decision contract. | Possible external orchestrator. Do not put quality logic in it. |
| Temporal | Durable workflows/retries. | Overkill; server/workers; not Act/Make wrapper. | Avoid for MVP. |
| cron / launchd | Simple local scheduling. | No evidence or agent contract. | Use underneath scheduling. |
| Buildkite agent | Jobs, status, logs, artifacts. | Tied to Buildkite control plane; CI runner, not local agent evidence layer. | Learn artifact/status patterns; not replacement. |
| Woodpecker / Drone / Jenkins | CI systems. | Too heavy; not Act-first local quality primitive. | Avoid as core. |
| mutmut / Stryker | Mutation testing. | Single gate type. | Future heavy-gate plugins. |
| OpenHands / Aider / CodeRabbit-like tools | AI coding/review. | Agents/reviewers, not local evidence runners. | Potential consumers of Foundry output. |

## Best architecture after research

Build Foundry, but keep it thin.

Do not build:

- a new CI system
- a new build graph engine
- a Dagger competitor
- a replacement for Make/Task/just/mise
- a replacement for Act
- a workflow UI like n8n

Build Foundry as a local evidence and decision facade over existing runners.

The novel layer is not “running commands.” Existing tools do that.

The novel layer is:

> A stable local contract that lets humans and agents ask: “Is this change acceptable according to this repo’s gates, and what evidence supports that answer?”

## Suggested MVP

### `foundry.yaml`

```yaml
version: 1

profiles:
  quick:
    gates:
      - id: lint
        run: make lint
      - id: unit
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
      - id: mutation
        run: make mutation
        allow_failure: true
        decision_on_failure: warn

schedules:
  nightly:
    profile: full
    cron: "0 2 * * *"
```

### CLI

```bash
foundry run quick
foundry run gha
foundry run full --json
foundry status
foundry latest --summary
```

### Output

```text
.foundry/runs/2026-06-15T12-00-00Z/
  result.json
  summary.md
  logs/
    lint.log
    unit.log
    github-actions.log
  artifacts/
  metadata.json
```

### `result.json`

```json
{
  "schema_version": 1,
  "run_id": "2026-06-15T12-00-00Z",
  "profile": "quick",
  "decision": "pass",
  "exit_code": 0,
  "started_at": "2026-06-15T12:00:00Z",
  "finished_at": "2026-06-15T12:03:14Z",
  "gates": [
    {
      "id": "lint",
      "runner": "shell",
      "command": "make lint",
      "status": "pass",
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

## Runner types to start with

Start with only:

1. shell
2. make
3. just
4. task
5. mise
6. act

Do not add Dagger initially, but leave a plugin path:

```yaml
- id: dagger-ci
  run: dagger call test
```

## Scheduler recommendation

Avoid a daemon for MVP.

Start with:

```bash
foundry schedule install nightly
foundry schedule list
foundry schedule remove nightly
```

Back it with:

- cron on Linux
- launchd on macOS

The important part is not inventing scheduling. The important part is ensuring scheduled runs emit the same evidence contract.

## Conclusion

Existing tools solve execution, task running, CI, workflow automation, or build graphing.

They do not appear to solve Foundry’s combined niche:

> local repo-defined quality gates + Act/GitHub Actions compatibility + scheduled profiles + artifact/log capture + structured agent-readable decision output.

So Foundry is worth exploring only if it stays focused on that contract.

Positioning:

> Foundry is the local quality evidence contract for AI-assisted development.

Not:

> Foundry is a new CI system.
