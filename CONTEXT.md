# Domain Glossary

## Gate
A verdict-producing command. Runs, exits, and its exit code determines pass/fail (0 = passed, non-zero = failed). The gate itself is dumb; Foundry adds the interpretation layer. Gates are composable with any tool.

## Profile
A named set of gates defined in `foundry.yaml`. Foundry's primary input — always a profile name, never a task description. Example: `foundry run post-feature`.

## Intent
What a developer wants achieved, expressed before it is decomposed into pipeline steps. PAS and Reckoner accept Intent in four source forms; all four resolve to the same internal representation before execution.

## SPEC file
A standardized software specification for a feature, bug, or new app. One of the four Intent source forms. Resolved by PAS via `pas generate <spec.md>`.

## PRD
A Product Requirements Document capturing user stories, goals, and requirements. Paired with a SPEC file as an Intent source. Resolved by PAS via `pas generate <prd.md> <spec.md>`.

## Beads Epic
A Beads container for a set of related tasks representing a complex feature. Used when work requires more than two tasks to complete. One of the four Intent source forms. Resolved by PAS via `pas scaffold <epic-id>`.

## Beads Issue
A single unit of work tracked in Beads. One of the four Intent source forms for targeted, single-task work.

## One-off Request
A natural language prompt expressing an Intent directly, without a structured file or tracked issue. Resolved by PAS via `pas plan --from-prompt` → `pas generate` → `pas run`.

## Working Clone
A developer's regular local git checkout of a repository. Distinct from Reckoner's internal bare repo. Used for post-PR branch handoff so the developer can continue work locally without re-pulling from remote.

## Decision
The outcome of a Profile run: `pass`, `warn`, `fail`, or `needs_human`. Derived from Gate results — any gate outputting the `FOUNDRY_NEEDS_HUMAN` sentinel forces `needs_human` regardless of exit codes. Drives whether Integrations fire.

## Integration
A configured side effect that fires when a run's Decision is `fail` or `needs_human` (never on `pass`/`warn`). Defined under `integrations` in `foundry.yaml`. Existing: `explain` (LLM writes `explanation.md`), `beads` (creates a Beads Issue), `agent` (dispatches a Claude Code session).

## Decision Point
A `needs_human` Decision specifically: something an agent cannot resolve on its own and must hand to a person for judgment. Distinct from `fail`, which an agent may be authorized to fix directly. A Decision Point is presented, never auto-resolved.
