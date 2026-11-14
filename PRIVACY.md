# Privacy

AI Usage is designed for local use and does not operate a remote service.

## What it reads

Depending on the provider, the local backend may access:

- **Codex:** nothing credential-related. It calls Hermes's own usage fetcher, which holds the sign-in.
- **Claude Code:** the OAuth token Hermes resolves for Anthropic, in memory, for one request.
- **Kimi and Z.ai:** the API keys `KIMI_API_KEY` / `KIMI_CODING_API_KEY` and `GLM_API_KEY` / `ZAI_API_KEY` / `Z_AI_API_KEY`, read from the active profile's secret scope.
- **Nous:** the account information returned by Hermes's Nous Portal helper (credit totals and plan).
- **Kimi Desktop log (off by default):** only when you turn on the `kimi_desktop_log_fallback` setting, up to 2 MB of Kimi Desktop's own local log file.
- Its own settings (`plugins.entries.usage-dashboard.settings`).

It does not read the Claude CLI's account file, `.env` files, `auth.json`, a browser profile or any other tool's credential store.

## Network requests

The credential a provider needs goes only to that provider's usage endpoint, and to no third party:

- `api.anthropic.com/api/oauth/usage` (Bearer token; `User-Agent: hermes-usage-dashboard/<version>`)
- `api.kimi.com/coding/v1/usages`
- `api.z.ai/api/monitor/usage/quota/limit` and `api.z.ai/api/biz/subscription/list`
- Codex and Nous requests are made by Hermes itself, not by this plugin.

There is no telemetry, analytics, advertising, crash reporting or remote logging, and the plugin never updates itself.

## Data sent to the desktop frontend

Only sanitized display data is returned: provider name, availability state, plan label, usage percentages, reset times, credit totals, whether a window caps a single model, alert thresholds, static error explanations and provider-neutral source labels. The frontend never receives credentials, authorization headers, email addresses, account IDs, OAuth claims or raw API responses.

## What it stores

In the plugin data directory (`plugin-data/usage-dashboard`, per profile):

- `availability.json`: per provider, a state (`ok`, `low`, `exhausted`, `unknown`), a rounded remaining percentage, a window name and a reset time. Other plugins and the `usage_availability` tool read it read-only. It holds no tokens, accounts or e-mail addresses.
- `anthropic-cache.json`: a sanitized copy of the last Claude usage card (5 minutes fresh, kept up to 24 hours as a rate-limit fallback).

In the Desktop plugin storage (per profile and connection):

- the last good sanitized usage payload (expires after 24 hours, at most 8 profiles kept),
- the highest alert level already announced for each quota window.

In the Desktop plugin storage, app-wide (shared by every profile and connection):

- the status-bar choice: Automatic or the pinned provider (a provider id and window label, no usage data).

No secret is stored by this plugin.

## Notifications

When a quota window crosses a threshold, the plugin shows an in-app toast and a native OS notification containing the provider name, window name, used percentage and reset time. Nothing else.

## Deletion

`hermes plugins remove usage-dashboard` removes the plugin. The data directory and the Desktop storage entries can then be deleted by hand.
