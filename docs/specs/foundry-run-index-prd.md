---
title: "Foundry — Run Index & Approval Gate"
status: draft
date: 2026-06-27
---

# PRD: Foundry — Run Index & Approval Gate

## Overview

Foundry runs quality gates and produces results. Those results live in per-repo `.foundry/runs/<run-id>/` directories — never aggregated, never surfaced unless you go look. This PRD covers three additions: a global SQLite run index written after every run, auto-explain on failure with a machine-readable next_action, and an `approval_required` flag that makes Zeno (or any external consumer) the dispatch gate instead of auto-firing agents immediately.

## Problem Statement

- Gate run results are scattered across `.foundry/runs/` in each repo. There is no cross-repo index.
- `foundry explain` is on-demand only. Failures go unexplained unless someone manually runs it.
- `integrations.agent` fires immediately on failure with no approval step. There is no way to require human (or Zeno) sign-off before an agent runs.
- External tools (Zeno, dashboards, scripts) have no stable, queryable record of what Foundry has done.

## Goals

1. After every `foundry run`, Foundry writes a row to `~/.foundry/foundry.db` (global SQLite index).
2. When a run produces `decision: fail`, Foundry auto-runs explain and writes explanation + one-sentence `next_action` to `foundry.db`.
3. When `integrations.agent.approval_required: true` in `foundry.yaml`, Foundry does NOT auto-fire the agent. It writes a `pending_approval` record and exits — leaving dispatch to the approval consumer.
4. After an approved agent run completes and a re-run passes, Foundry marks the next_action `resolved` in `foundry.db`.

## Non-Goals

- Zeno UI, inbox, or report changes — those are in the Zeno PRD
- Reckoner integration
- Remote or cloud storage of run data
- Real-time push or webhooks — all access is pull (read SQLite)
- Multi-user or multi-machine aggregation

## Functional Requirements

**FR-3 — Foundry SQLite index:** Foundry writes `~/.foundry/foundry.db` after every `foundry run`. Schema includes a `runs` table (run_id, repo_path, profile, decision, timestamps, gate_count, failed_gates, result_path, summary_path) and a `next_actions` table. This is a global cross-repo index. Prune: runs older than 90 days; resolved next_actions older than 30 days.

**FR-4 — Auto-explain on failure:** When `decision: fail`, Foundry automatically runs explain and extracts a one-sentence `next_action` via LLM (haiku). Writes both to `next_actions` table. Explain failure must never block the run — log warning and continue.

**FR-10 — Approval gate:** When `integrations.agent.approval_required: true` in `foundry.yaml`, on failure Foundry writes to `next_actions` with `status: pending_approval` and does NOT fire `integrations.agent.command`. Existing auto-fire behavior is unchanged when flag is absent or false.

**FR-11 — Resolve on re-run pass:** When `foundry run` produces `decision: pass` and there are `approved` next_actions for the same repo+profile, Foundry marks them `resolved` with a `resolved_at` timestamp.

## Data

| Table | Purpose |
|-------|---------|
| `runs` | One row per `foundry run` execution, cross-repo |
| `next_actions` | Failure explanations + next_action text, with approval/resolve lifecycle |

Location: `~/.foundry/foundry.db` (user-global, not per-repo).

## Success Criteria

- After `foundry run quick` on any repo, `~/.foundry/foundry.db` has a new row in `runs`.
- After a failing run, `next_actions` has a row with `explanation` and `next_action` populated within the same process exit.
- With `approval_required: true`, no agent fires automatically on failure.
- After an approved re-run passes, the next_action row shows `status: resolved`.
- Explain failure (no API key, timeout) produces a warning log but does not fail the run.

## Build Phases

| Phase | Scope | Files changed |
|-------|-------|--------------|
| 2 | SQLite index writer | `output.py` — add `write_run_index` |
| 3 | Auto-explain on failure | `runner.py`, `explain.py`, `output.py`, `config.py` |
| 5 (Foundry side) | Resolve on re-run pass | `output.py` — add `resolve_next_action_for_run`; `config.py` — `approval_required` |
