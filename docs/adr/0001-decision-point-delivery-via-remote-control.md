# Decision Point delivery via Claude Code Remote Control

Foundry needs a way to hand a `needs_human` Decision Point to the user wherever they are, without building new notification infrastructure. We decided to dispatch a review-only Claude Code session (`integrations.agent.review_command`) that calls `PushNotification` when it judges the Decision Point genuinely needs a person — relying on Remote Control's account-level pairing (verified via `claude-code-remote` MCP proxy logs, not tied to the spawning terminal) to reach the user's phone/desktop from a headless, backend-spawned session.

## Considered Options

- **Zeno-side push** (Twilio SMS, email digest, a mobile push service) — rejected: Zeno's Inbox already exists as the review surface, but real-time delivery would mean building and maintaining a separate notification channel Foundry doesn't otherwise need.
- **New Foundry event bus** — rejected as disproportionate: the existing `_fire_integrations` hook already fires on the right Decisions; the gap was a missing action (review-and-notify), not a missing transport.
- **Keep polling Zeno's twice-daily digest** — rejected: too slow for something the user described as wanting to react to promptly, from a phone.

## Consequences

- Decision Point delivery now has a hard dependency on Claude Code's Remote Control feature and account pairing. **Verified** (foundry-uhj.2): a `claude -p` session spawned headlessly via `subprocess.Popen(["sh", "-c", cmd], start_new_session=True)` — the same mechanism `review_command` uses, not an interactive terminal — successfully reaches the account-scoped Remote Control backend and invokes `PushNotification`. Two independent runs both got specific, structured telemetry back (exact idle-seconds vs. a 60s threshold) rather than a connection or tool-not-found error, confirming pairing carries over to backend-spawned subprocesses as assumed.
- Delivery is additionally gated by a presence-detection feature: the backend suppresses the actual push if the account looks recently active, tracked against Claude Code session activity broadly (not raw OS idle time) — both verification runs were suppressed this way despite the physical machine being idle, because the verification itself ran from an active foreground session. This is orthogonal to the headless-vs-interactive question this ADR depends on, and favors Foundry's real usage: `review_command` fires from a background/scheduled run, typically while the user's session is *not* active, so the presence gate should not suppress it in practice. If a real-world push is ever suppressed or missed for any reason (including this gate), `next_actions` is still the durable fallback record.
- `review_command` is deliberately never authorized to fix-and-continue; only `command` (on `fail`) can. Swapping this later means rewriting every repo's review-oriented gate prompts, not just a config flag.
