# Foundry

A gate-based CI pipeline runner for local and scheduled workflows. Define quality gates in `foundry.yaml`, run them locally, and schedule them as recurring checks — no external CI required.

## Install

```bash
uv add foundry-lite
```

Or from source:

```bash
cd foundry-lite
uv sync
uv run foundry --help
```

## Quick start

```bash
foundry init          # scaffold foundry.yaml in current repo
foundry doctor        # verify config and dependencies
foundry run default   # run the default profile
foundry latest        # show result of last run
```

## foundry.yaml

```yaml
version: 1

profiles:
  default:
    gates:
      - id: lint
        run: ruff check .
      - id: test
        run: pytest
        timeout: 5m

schedules:
  nightly:
    profile: default
    cron: '0 2 * * *'

integrations:
  explain:
    on_failure: true
    model: claude-sonnet-4-6
```

## Gates

Each gate runs a shell command. Gates are **fail-fast** by default — first failure stops the profile.

| Field | Default | Description |
|-------|---------|-------------|
| `run` | — | Shell command to execute |
| `timeout` | `10m` | Max runtime (`s`, `m`, `h`) |
| `allow_failure` | `false` | Continue profile on failure |
| `decision_on_failure` | `fail` | `fail` or `warn` |

Emit `FOUNDRY_NEEDS_HUMAN` from any gate to pause and request human review.

## Container isolation

Add a `docker:` block to a profile to run all its `run:` gates inside a container. Gates using `act:` manage their own containers and are unaffected.

```yaml
profiles:
  security:
    docker:
      image: ghcr.io/citadelgrad/foundry-runner:latest
      volumes:
        - ~/.claude:/home/node/.claude:ro   # subscription credentials, read-only
    gates:
      - id: owasp-scan
        run: claude -p "/security-review" --dangerously-skip-permissions
        timeout: 30m
        allow_failure: true
      - id: ubs-scan
        run: ubs . --profile=strict --format=toon
        timeout: 30m
        allow_failure: true
```

Gate-level `docker:` overrides the profile default for that gate only.

The `foundry-runner` image (`ghcr.io/citadelgrad/foundry-runner:latest`) ships with `claude-code` and `ubs`/`tru` pre-installed. Auth is handled via the `~/.claude` volume mount — no API keys required.

## CLI reference

```
foundry run <profile>              # run a profile
foundry run <profile> --dry-run    # preview gates without running
foundry doctor                     # check config and environment
foundry latest                     # last run result
foundry explain                    # AI explanation of last failure
foundry schedule install <name>    # install cron from foundry.yaml
foundry schedule list              # list installed schedules
foundry schedule remove <name>     # remove a schedule
foundry daemon start               # persistent scheduler daemon
foundry daemon stop
foundry daemon status
```

## Integrations

**explain** — on failure, calls an AI model and writes `explanation.md` into the run directory.

**agent** — runs an arbitrary shell command (e.g. `claude -p "..."`) after failure, with `{run_dir}` interpolated. See [Decision Points: `command` vs `review_command`](#decision-points-command-vs-review_command) below for the two dispatch paths.

**beads** — opens a beads issue on failure or when human review is needed.

## Decision Points: `command` vs `review_command`

A gate's outcome maps to one of four decisions: `pass`, `warn`, `fail`, or `needs_human` (emitted when any gate prints `FOUNDRY_NEEDS_HUMAN` to stdout — see [Gates](#gates)). `integrations.agent` dispatches to two different commands depending on which of those last two decisions fired, and the two are never interchangeable:

| Decision | Field fired | Intent |
|----------|------------|--------|
| `fail` | `command` (requires `on_failure: true`) | Autonomous fix-and-continue — the agent is authorized to make changes |
| `needs_human` | `review_command` (requires `on_needs_human: true`) | Review-only — inspect evidence, notify a human, take no action |

`fail` means a gate failed and no judgment call is needed — an agent can go fix it. `needs_human` means a gate itself decided the situation requires a person (e.g. a review-panel gate judged the risk ambiguous). Because of that distinction, `review_command` is never authorized to fix or continue on its own — swapping that later means rewriting every repo's review-oriented gate prompts, not just a config flag. `command` never fires for `needs_human`, and `review_command` never fires for `fail`/`warn`/`pass`.

```yaml
integrations:
  agent:
    on_failure: true
    command: 'claude -p "Foundry gate failed. Diagnose and fix. See {run_dir}/result.json" --dangerously-skip-permissions'
    approval_required: false   # if true, `command` is held back pending human approval of the next_action

    on_needs_human: true
    review_command: 'claude -p "$(cat <<PROMPT
      A Foundry gate flagged this run as needs_human — a Decision Point that
      requires a person, not an autonomous fix.

      Read {run_dir}/result.json and {run_dir}/explanation.md (if present) to
      understand what the gate observed and why it judged this ambiguous.

      Your job is strictly REVIEW-ONLY:
      - Do not modify any files.
      - Do not attempt to fix, continue, retry, or unblock the run.
      - Only call the PushNotification tool, and only if you conclude a human
        genuinely needs to weigh in now. Summarize the decision at stake in
        the notification body so it is actionable from a phone.
      - If the evidence does not actually warrant a human decision, take no
        action and exit.
      PROMPT
      )" --dangerously-skip-permissions'
```

Both `command` and `review_command` are dispatched the same way — `subprocess.Popen(["sh", "-c", cmd], start_new_session=True)`, a detached background process, not an interactive terminal — with `{run_dir}` interpolated into the string before execution.

### Review panel gate: judging whether a Decision Point is warranted

`review_command` only fires once a gate has already decided the run `needs_human`. The judgment call — *should* this run need a human? — has to happen earlier, inside a gate. This is the "review panel" pattern: a gate pipes some evidence (a diff, a failing test, a scan result) to `claude -p` and asks it to decide, then echoes `FOUNDRY_NEEDS_HUMAN` to stdout only if the model concludes the risk is ambiguous enough to need a person.

Contrast this with the `security-review` gate pattern (`owasp-scan` in this repo's own `foundry.yaml`): that gate runs an LLM and reports pass/fail via `allow_failure`/`decision_on_failure: warn`, but it never makes a judgment call about whether a *human* specifically needs to look — it's a scanner, not a review panel. The pattern below adds that judgment layer on top of a scan or diff:

```yaml
profiles:
  release-review:
    gates:
      - id: risk-review-panel
        run: |
          git diff origin/main...HEAD > /tmp/review-diff.txt
          VERDICT=$(claude -p "$(cat <<PROMPT
            You are a release review panel. Read this diff and judge whether
            it needs a human decision before shipping — e.g. touches auth,
            payments, data deletion, external API contracts, or has any
            change whose blast radius you can't fully assess from the diff
            alone.

            Diff:
            $(cat /tmp/review-diff.txt)

            Respond with exactly one word: NEEDS_HUMAN or SAFE.
          PROMPT
          )" --print --dangerously-skip-permissions)

          echo "$VERDICT"
          if [ "$VERDICT" = "NEEDS_HUMAN" ]; then
            echo "FOUNDRY_NEEDS_HUMAN"
          fi
        timeout: 10m
        allow_failure: true
        decision_on_failure: warn

integrations:
  agent:
    on_needs_human: true
    review_command: 'claude -p "Review {run_dir}/result.json and {run_dir}/explanation.md. This is review-only — inspect the evidence and call PushNotification only if a human decision is genuinely needed. Do not fix or continue anything." --dangerously-skip-permissions'
```

`derive_decision` treats any gate that printed `FOUNDRY_NEEDS_HUMAN` as taking priority over pass/fail/warn for the whole profile, so `risk-review-panel` alone is enough to flip the run's decision to `needs_human` and trigger `review_command`.

### Remote Control reachability from headless dispatch

`review_command` is spawned the same way as `command` — a detached background process with no TTY, not an interactive terminal session. This has been verified to work: a `claude -p ... --dangerously-skip-permissions` session launched exactly this way successfully reaches Claude Code's Remote Control backend and can call `PushNotification` — confirmed across two live test runs that returned specific backend telemetry rather than a connection or tool-not-found error. Remote Control pairing is account-level, not tied to the spawning terminal, so it carries over to backend-spawned subprocesses.

One caveat: delivery is separately gated by a presence-detection feature — the backend can suppress the actual push if the account looks recently active (tracked via Claude Code session activity, not raw OS idle time). This is not expected to matter in practice for `review_command`, since it fires from background/scheduled Foundry runs, typically while the user's own session is inactive. If a push is ever suppressed or missed for any reason, the `next_actions` record in the run index (see [Run index](#run-index)) remains the durable fallback. Full details: [`docs/adr/0001-decision-point-delivery-via-remote-control.md`](docs/adr/0001-decision-point-delivery-via-remote-control.md).

## Run index

Every run is persisted to a local SQLite database (`~/.foundry/runs.db`). Query history with `foundry latest` or inspect directly.

## License

MIT
