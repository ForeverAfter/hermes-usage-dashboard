# User Guide

## Overview

Hermes Usage Dashboard places an **AI Usage** indicator in the Hermes Desktop status bar. The percentage represents the remaining quota for the provider associated with the focused session.

## Status-bar modes

### Automatic

Automatic mode follows the provider and model reported by the active Hermes session:

- OpenAI or Codex models → OpenAI Codex
- Anthropic or Claude models → Claude Code
- Kimi or Moonshot models → Kimi / Moonshot
- GLM or Z.ai models → ZCode / Z.ai
- Hermes or Nous models → Nous Research

If the active provider does not expose a percentage, the status bar shows `AI —`. It never substitutes another provider silently.

Within a provider, the chip uses the same rule as `availability.json`: an exhausted window (1 % left or less) comes
first, and if several are exhausted, the one that blocks longest; otherwise the lower of the session (5 h) and weekly
windows. An exhausted window reads `AI 0% · exhausted`, so a healthy session never hides an exhausted week. A
session or weekly window without a percentage shows `AI —` unless another window is exhausted. The Opus / Sonnet
weekly cap of the model the session is running replaces that window only when it is stricter.

### Pinned

Click any percentage in the popover to pin that provider and window. The choice persists locally. Click **Follow session** to return to automatic mode.

## Provider cards

Each card may contain:

- Plan name, when exposed by the provider
- One or more usage windows
- Remaining percentage
- Reset time
- Credit totals or provider-specific details
- Data source and fetch time

## Data states

| Badge | Meaning |
| --- | --- |
| `ACTIVE` | The provider returned current data. |
| `STALE` | The latest request failed; the last valid sanitized value is shown. |
| `LOCAL` | Data came from a local application log rather than a live provider API. |
| `NO DATA` | No compatible session, credential, or usage response is available. |

A stale or local value is never presented as current.

## Refresh behavior

The **Refresh** button requests the backend cache. Provider calls are protected by short cache windows to avoid HTTP 429 rate limits. Repeated clicks do not force repeated upstream requests.

## Troubleshooting

### The indicator shows `AI —`

- Confirm the focused Hermes session uses a supported provider.
- Open the popover and inspect the provider reason.
- Confirm the provider is authenticated in Hermes.
- Run `hermes plugins doctor usage-dashboard`.

### The popover cannot load usage

- Confirm the plugin is enabled in the active Hermes profile and that Hermes is 0.22.5 or newer.
- Restart the profile gateway after installation or updates.
- If the **AI** chip is missing, run **Reload desktop plugins** from the command palette and
  `hermes plugins doctor usage-dashboard` (it reports a stale desktop copy).
- A card that says "requires an active Hermes profile scope" means Hermes did not bind a profile for that request;
  the plugin never creates one itself. Update Hermes.

### Claude shows `STALE`

Anthropic may temporarily rate-limit the usage endpoint even while Claude Code itself continues to work. The plugin keeps the last valid card for up to 24 hours and retries later.

### Kimi shows `LOCAL`

The live Kimi endpoint was unavailable or no Kimi Coding key was found, and you turned on **Read the Kimi Desktop log**
in the plugin's settings (it is off by default). The plugin then parses the latest Kimi Desktop subscription log.
Open Kimi Desktop to refresh that local state.

### A provider is missing a monthly or weekly card

The provider did not expose that window. The plugin deliberately avoids inferring periods that are not present in the upstream response.

## Profiles

The status bar, its cached values and its alerts are kept per profile and per connection: switching profile never
shows another profile's quota. Install and enable the plugin in each profile that should expose usage.

## Alerts and settings

Under **Settings → Plugins → AI Usage** you can change:

- the warning and critical thresholds (80 % and 95 % used by default) and turn alerts off;
- the "low quota" threshold used for `availability.json` (15 % remaining by default);
- whether Kimi Desktop's log may be read as a fallback (off by default).

When a window crosses a threshold you get an in-app toast and, while Hermes is in the background, a native notification.
Model-capped windows (Opus / Sonnet weekly) never raise alerts.

## Asking an agent what quota is left

The `usage_availability` tool (and the `/usage-availability` command in the CLI and messaging gateways) reports which
subscriptions have quota and when an exhausted one comes back. The data comes from `availability.json`, refreshed
while Hermes Desktop is open; anything older than 15 minutes is reported as `unknown`.
