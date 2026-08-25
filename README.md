# Hermes Usage Dashboard

[![CI](https://github.com/ForeverAfter/hermes-usage-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/ForeverAfter/hermes-usage-dashboard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A privacy-conscious Hermes Desktop plugin that displays subscription usage for:

- OpenAI Codex / ChatGPT plans
- Claude Code subscriptions
- Kimi / Moonshot Coding plans
- ZCode / Z.ai plans
- Nous Research credits

The plugin adds a compact **AI Usage** status-bar indicator and a scrollable popover. It can automatically follow the provider used by the active Hermes session or pin a specific provider window.

> [!IMPORTANT]
> This is a community plugin. It is not affiliated with or endorsed by OpenAI, Anthropic, Moonshot AI, Z.ai, Nous Research, or the Hermes Agent project.

## Features

- Automatic provider detection from the focused Hermes session
- Manual pinning of any available usage window
- Protected refreshes that avoid repeatedly hitting provider APIs
- Per-provider stale-data fallback instead of misleading zero values
- Shared Claude usage cache to tolerate temporary Anthropic HTTP 429 responses
- Explicit states for active, stale, local, and unavailable data
- Credentials remain in the local Python backend
- No analytics, telemetry, account identifiers, or credential logging

## Requirements

- Hermes Agent with Hermes Desktop plugin support
- Python 3.11 or newer
- An authenticated account for each provider you want to display
- Internet access for provider usage endpoints

## Installation

### 1. Install and enable the backend

```bash
hermes plugins install ForeverAfter/hermes-usage-dashboard --enable
```

### 2. Install the desktop UI

**Windows PowerShell**

```powershell
python "$env:LOCALAPPDATA\hermes\plugins\usage-dashboard\scripts\install_desktop.py"
```

**macOS or Linux**

```bash
python "${HERMES_HOME:-$HOME/.hermes}/plugins/usage-dashboard/scripts/install_desktop.py"
```

If you use a named Hermes profile, set `HERMES_HOME` to that profile before running the commands.

### 3. Reload

Restart the Hermes gateway so the Python route is mounted. Hermes Desktop watches desktop plugins automatically; if the indicator does not appear, run **Reload desktop plugins** from the command palette.

### 4. Verify

```bash
hermes plugins doctor usage-dashboard
```

The status bar should show `AI —` until usage data is available, then `AI <remaining>%`.

## Usage

1. Click the **AI** status-bar indicator.
2. Review provider windows, percentages, reset times, plans, and credit details.
3. Leave the selector on **Automatic** to follow the focused session.
4. Click any percentage to pin it.
5. Click **Follow session** to return to automatic mode.
6. Use **Refresh** when needed. Refreshes are cache-protected to reduce rate limiting.

See the full [User Guide](docs/USER_GUIDE.md).

## Provider caveats

Provider APIs are not standardized. Some expose multiple percentage windows; others expose credits or only local desktop state. This plugin does not invent missing monthly, weekly, or five-hour windows. See [Provider Notes](docs/PROVIDER_NOTES.md).

## Privacy and security

- OAuth tokens and API keys are resolved by Hermes and used only in the local backend.
- The frontend receives sanitized usage percentages, reset times, plan labels, and credit totals.
- Email addresses, account IDs, claims, raw API responses, headers, and tokens are not returned.
- No telemetry is collected.
- A local sanitized cache is used for resilience and expires automatically.

Read [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md) before installation.

## Development

```bash
python -m pip install -r requirements-dev.txt
python scripts/security_scan.py
python -m pytest
python -m ruff check dashboard scripts tests
python -m compileall -q dashboard scripts tests
node --check desktop/plugin.js
```

Architecture details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Contributions are welcome under [CONTRIBUTING.md](CONTRIBUTING.md).

## Uninstall

**Windows PowerShell**

```powershell
python "$env:LOCALAPPDATA\hermes\plugins\usage-dashboard\scripts\uninstall_desktop.py"
hermes plugins remove usage-dashboard
```

**macOS or Linux**

```bash
python "${HERMES_HOME:-$HOME/.hermes}/plugins/usage-dashboard/scripts/uninstall_desktop.py"
hermes plugins remove usage-dashboard
```

Restart the gateway after removal.

## License

MIT — see [LICENSE](LICENSE).
