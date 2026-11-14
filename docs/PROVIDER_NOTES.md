# Provider Notes

Provider usage APIs are not standardized and may change without notice.

## OpenAI Codex

- Read through Hermes's own usage fetcher (`fetch_account_usage("openai-codex", read_only=True)`). The plugin never
  holds or refreshes the ChatGPT sign-in, and the read never rotates a credential.
- Hermes labels the windows by their published duration (`Session`, `Weekly`).
- ChatGPT/Codex subscription limits are not the same as OpenAI API billing.

## Claude Code

- Requires an OAuth-backed Claude account.
- Uses Anthropic's OAuth usage endpoint with the token Hermes resolves. The plugin sends it only to
  `api.anthropic.com` and identifies itself as `hermes-usage-dashboard`, not as another vendor's client.
- The plugin parses the response itself because Hermes core still multiplies any `utilization` of 1 or less by 100
  (upstream #58226), which would show a fresh 1 % session as 100 % used. It switches to the core fetcher once that
  is fixed.
- May expose current-session and current-week windows, plus model-specific windows (Opus / Sonnet week). Those are
  marked as model caps: they never decide the status bar, the summary or `availability.json` unless the session runs
  that model.
- The plan label is not shown: the plugin does not read the Claude CLI's account file.
- Anthropic can return HTTP 429 for the usage endpoint while Claude Code inference still works. A sanitized launch-profile
  cache (plugin data directory) reduces duplicate requests; requests for another profile never use it.
- OAuth inference and quota access are separate: a working inference credential does not guarantee permission to read usage.

## Kimi / Moonshot

- Prefers the Kimi Coding usage endpoint, with the key Hermes already has, read through the profile's secret scope.
  `KIMI_CODING_API_KEY` is tried first and a Kimi Code key (`sk-kimi-…`) before any other; the next declared key
  (`KIMI_API_KEY`) is tried only when Kimi rejects the previous one as unauthorized (HTTP 401/403). A Moonshot
  platform key in `KIMI_API_KEY` therefore no longer hides a valid Kimi Coding key. Keys are never logged or returned.
- Reading Kimi Desktop's local log is **off by default**. When the `kimi_desktop_log_fallback` setting is on and the
  API is unavailable, the plugin parses up to 2 MB of that log. The data is marked `LOCAL` and becomes stale after 24 hours.
- The log belongs to the operating-system user, not an independently scoped account. Do not assume complete
  cross-account isolation when using it.
- Log timestamps without an explicit offset are interpreted in the machine's local timezone.
- The top-level documented Kimi bucket is labeled weekly; no monthly quota is inferred.

## ZCode / Z.ai

- Uses the Z.ai quota endpoint with `GLM_API_KEY`, `ZAI_API_KEY` or `Z_AI_API_KEY`, read through the profile's secret scope.
- Window semantics are assigned only for verified type/unit/number combinations.
- Unknown combinations receive a generic `Plan limit` label.
- Subscription metadata is optional and failure to fetch it does not hide quota data.

## Nous Research

- Uses the signed-in Nous Portal account helper provided by Hermes (the one remaining non-public core import).
- Displays subscription credits, top-up credits, usable total, rollover, and renewal details when exposed.
- Credits are shown as credits. A percentage is calculated only when both total and remaining values are verifiable.

## Availability policy

Missing data is represented as unavailable. The plugin never converts missing data into zero percent used or one hundred percent remaining.
`availability.json` follows the same rule: no data, data older than 15 minutes, or data stamped more than 60 seconds
in the future, is `unknown`.
