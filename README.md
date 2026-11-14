# AI Usage for Hermes

[![CI](https://github.com/ForeverAfter/hermes-usage-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/ForeverAfter/hermes-usage-dashboard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

See how much of your **Codex, Claude Code, Kimi, Z.ai and Nous** subscriptions is left, from the Hermes Desktop status bar, and let your agents ask the same question. Spanish version: [README.es.md](README.es.md).

<!-- screenshot: docs/images/status-chip.png (status-bar chip "AI 64%" and the open popover) -->

- **Follows your session.** The chip shows the quota of the provider the focused session is actually using, or a provider you pin (one app-wide choice). Opus/Sonnet weekly caps never pose as your account quota.
- **Never lies when a provider is down.** Stale values are badged `STALE`, local ones `LOCAL`, missing ones `NO DATA`. A window reset invalidates the backend's fresh caches; the fallback copies shown while a provider or the backend is down (saved ones for up to 24 hours) can still show the previous window, always marked `STALE` or as cached data. Claude's 1 % session is shown as 1 %, not 100 %.
- **Agents can ask.** The `usage_availability` tool and the `/usage-availability` command say which subscription still has quota and when an exhausted one comes back. Alerts at 80 % and 95 % used.

## Install

```bash
hermes plugins install ForeverAfter/hermes-usage-dashboard --enable
```

That is all: Hermes Desktop loads the chip from the installed plugin (if it does not appear, run **Reload desktop plugins** from the command palette). Requires Hermes 0.22.5 or newer. Check with `hermes plugins doctor usage-dashboard`.

Kimi and Z.ai use the keys Hermes already has (`KIMI_CODING_API_KEY` or `KIMI_API_KEY`, the Kimi Coding key first; `GLM_API_KEY` or `ZAI_API_KEY`). Codex, Claude and Nous use the sign-in you already did in Hermes (`hermes auth`).

## What it reads / what it sends

Everything runs on your machine. There is no telemetry, analytics or remote logging, and no auto-update.

| Provider | What the plugin reads | Where it sends a request |
| --- | --- | --- |
| OpenAI Codex | Hermes's own usage fetcher (read-only; the plugin never sees the ChatGPT sign-in) | Made by Hermes to the ChatGPT backend |
| Claude Code | The OAuth token Hermes resolves for Anthropic, in memory, for one request | `api.anthropic.com/api/oauth/usage` (Bearer token, `User-Agent: hermes-usage-dashboard/<version>`) |
| Kimi | `KIMI_API_KEY` / `KIMI_CODING_API_KEY` from the profile's secret scope | `api.kimi.com/coding/v1/usages` |
| Z.ai | `GLM_API_KEY` / `ZAI_API_KEY` / `Z_AI_API_KEY` from the profile's secret scope | `api.z.ai/api/monitor/usage/quota/limit` and `api.z.ai/api/biz/subscription/list` |
| Nous | Hermes's Nous Portal account helper | Made by Hermes to the Nous Portal |
| Kimi Desktop log | Only if you turn on `kimi_desktop_log_fallback` (off by default): up to 2 MB of Kimi Desktop's own log | Nothing |

Credentials never reach the interface, the logs, `availability.json` or any cache. It does **not** read the Claude CLI's account file, `.env`, `auth.json` or any other tool's credentials.

It writes only inside its own data (`plugin-data/usage-dashboard`: `availability.json` and a Claude usage cache) and in the Desktop plugin storage (the last good values and alert memory per profile and connection, plus your pinned provider, which is app-wide). Details: [PRIVACY.md](PRIVACY.md), [SECURITY.md](SECURITY.md).

## For agents and other plugins

`availability.json` is published after every collection, with facts only:

```json
{"schema": 1, "generated_at": "2026-11-06T07:52:04Z",
 "providers": {"anthropic": {"state": "ok", "remaining_percent": 77, "window": "session",
                             "reset_at": "2026-11-06T11:50:00Z", "stale": false}}}
```

States are `ok`, `low` (15 % left or less by default), `exhausted` (1 % or less) and `unknown` (no data, or older than 15 minutes). Provider keys are Hermes provider ids. Read it read-only and treat a missing or old file as `unknown`, never as a block. It refreshes while Hermes Desktop is open.

## Settings

**Settings → Plugins → AI Usage**: warning and critical alert thresholds, the low-quota threshold, and the optional Kimi Desktop log.

## Usage

1. Click the **AI** status-bar chip to see every provider, window, plan and reset time.
2. Leave it on **Automatic** to follow the focused session, or click any percentage to pin its provider (the chip then shows that provider's exhausted or lowest session/weekly window; the pin is app-wide). **Follow session** goes back.
3. **Refresh** is cache-protected to avoid provider rate limits.

See the [User Guide](docs/USER_GUIDE.md) and [Provider Notes](docs/PROVIDER_NOTES.md).

> [!IMPORTANT]
> Community plugin, not affiliated with or endorsed by OpenAI, Anthropic, Moonshot AI, Z.ai, Nous Research or the Hermes Agent project. Anthropic's usage endpoint is read with the plugin's own parser only because Hermes core still misreads values of 1 % or less (upstream #58226); it will move to the core fetcher once that is fixed.

## Development

```bash
python -m pip install -r requirements-dev.txt
python scripts/security_scan.py
python -m pytest
python -m ruff check .
node --check desktop/plugin.js
```

Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md). Changes: [CHANGELOG.md](CHANGELOG.md).

## Uninstall

```bash
hermes plugins remove usage-dashboard
```

`scripts/legacy/` holds the old desktop copier for Hermes older than 0.22; do not use it on supported versions.

## License

MIT — see [LICENSE](LICENSE).
