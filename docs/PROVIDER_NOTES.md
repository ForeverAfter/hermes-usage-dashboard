# Provider Notes

Provider usage APIs are not standardized and may change without notice.

## OpenAI Codex

- Uses Hermes-compatible local Codex OAuth credentials.
- Classifies windows only when the upstream duration is explicit.
- Recognizes five-hour, weekly, and approximately monthly durations.
- Falls back to a generic `Codex limit` label for unknown durations.
- ChatGPT/Codex subscription limits are not the same as OpenAI API billing.

## Claude Code

- Requires an OAuth-backed Claude account.
- Uses Anthropic's OAuth usage endpoint through Hermes.
- May expose current-session and current-week windows, plus model-specific windows when available.
- Anthropic can return HTTP 429 for the usage endpoint while Claude Code inference still works.
- A sanitized launch-profile cache reduces duplicate usage requests. Requests with an existing secret scope bypass that cache to avoid reusing another account's data.
- Parallel collectors preserve Hermes profile context; unscoped launch requests use the core's launch-profile policy without disabling fail-closed protection.
- OAuth inference and quota access are separate: a working inference credential does not guarantee permission to read usage.

## Kimi / Moonshot

- Prefers the Kimi Coding usage endpoint.
- Uses the configured Kimi Coding credential resolved by Hermes.
- May fall back to a local Kimi Desktop log when live data is unavailable.
- Local log data is marked `LOCAL` and becomes stale after 24 hours.
- This fallback belongs to the operating-system user, not an independently scoped account. Do not assume complete cross-account isolation when using it.
- Log timestamps without an explicit offset are interpreted in the machine's local timezone.
- The top-level documented Kimi bucket is labeled weekly; no monthly quota is inferred.

## ZCode / Z.ai

- Uses the Z.ai quota endpoint with a credential resolved by Hermes.
- Window semantics are assigned only for verified type/unit/number combinations.
- Unknown combinations receive a generic `Plan limit` label.
- Subscription metadata is optional and failure to fetch it does not hide quota data.

## Nous Research

- Uses the signed-in Nous Portal account helper provided by Hermes.
- Displays subscription credits, top-up credits, usable total, rollover, and renewal details when exposed.
- Credits are shown as credits. A percentage is calculated only when both total and remaining values are verifiable.

## Availability policy

Missing data is represented as unavailable. The plugin never converts missing data into zero percent used or one hundred percent remaining.
