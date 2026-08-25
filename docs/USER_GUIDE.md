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

- Confirm the plugin is enabled in the active Hermes profile.
- Restart the profile gateway after installation or updates.
- Confirm the desktop UI installer was run for the same `HERMES_HOME`.

### Claude shows `STALE`

Anthropic may temporarily rate-limit the usage endpoint even while Claude Code itself continues to work. The plugin keeps the last valid card for up to 24 hours and retries later.

### Kimi shows `LOCAL`

The live Kimi endpoint was unavailable or no Kimi Coding key was found. On supported systems the plugin may parse the latest Kimi Desktop subscription log. Open Kimi Desktop to refresh that local state.

### A provider is missing a monthly or weekly card

The provider did not expose that window. The plugin deliberately avoids inferring periods that are not present in the upstream response.

## Profiles

Hermes profiles have separate plugin configuration and desktop directories. Install and enable the backend for each profile that should expose usage, and run the desktop installer with that profile's `HERMES_HOME` when using an isolated desktop plugin directory.
