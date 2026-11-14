# Changelog

All notable changes to this project are documented here.

## [1.4.1] - 2026-11-14

Fixes from the 1.4.0 review. No change to the `availability.json` contract (schema 1), tool or command names.

### Fixed

- Kimi: `KIMI_CODING_API_KEY` (and any `sk-kimi-` key) is tried before `KIMI_API_KEY`, and the next declared key is
  tried only when Kimi rejects the previous one with HTTP 401/403. A Moonshot platform key no longer hides a valid
  Kimi Coding key. Keys are never logged or returned.
- Status chip: it follows the same policy as `availability.json`. An exhausted session or weekly window comes first
  (the one that blocks longest), otherwise the lowest of session and week; the cap of the model in use still wins
  only when it is stricter. An exhausted window reads `AI 0% · exhausted`, so a healthy session no longer hides an
  exhausted week.
- `usage_availability` / `/usage-availability`: a file whose `generated_at` is more than 60 seconds in the future
  reads as `unknown`, and a `remaining_percent` that is not `null` or a finite number from 0 to 100 (`1e999`, `NaN`,
  booleans) makes that provider `unknown` instead of failing the whole read.
- Alerts: a window that is already critical the first time it is seen is announced once; reset times up to 5 minutes
  apart are the same window (no repeat alerts from timestamp jitter); with two sources exposing the same profile
  name, the in-memory alert state is kept per activation epoch.

### Changed

- Docs: pinning keeps a provider, not a window; the pinned provider is one app-wide preference; model caps can only
  change the status-bar chip, never the summary, alerts or `availability.json`.

## [1.4.0] - 2026-11-06

Catalog-admission release. Requires Hermes 0.22.5 or newer.

### Added

- `availability.json` in the plugin data directory, rewritten after every collection: per provider `ok` / `low` / `exhausted` / `unknown`, remaining percent, governing window and reset time. Facts only, no tokens or accounts.
- Agent tool `usage_availability` and command `/usage-availability`: which subscription still has quota and when an exhausted one comes back.
- Threshold alerts: an in-app toast and a native notification when a window crosses the warning (80 %) or critical (95 %) used percent. Thresholds, the low-quota threshold and alerts on/off are plugin settings.
- `plugin.yaml`: `requires_hermes`, `license`, `homepage`, manifest v2, `config_schema`, optional env declarations and the declared tool.
- English README, Spanish README (`README.es.md`) and a rewritten PRIVACY with a "what it reads / what it sends" table.
- Window `scope` (`account` or `model`) in the response; model-capped windows are labelled "(model cap)".
- Tests: scope isolation of the frontend cache, discarding of late responses, model caps, alerts, the availability contract and a no-credential regression over every card and published file.

### Changed

- Codex is read through Hermes's public usage fetcher (`read_only=True`); the plugin no longer holds the ChatGPT sign-in.
- Kimi and Z.ai keys are read with `get_secret` from the profile's secret scope instead of the runtime provider resolver.
- Reading the Kimi Desktop log is off by default (`kimi_desktop_log_fallback`).
- The Claude usage cache moved to the plugin data directory.
- The frontend cache, query key and alert memory are keyed by profile and connection; the unscoped cache from earlier versions is removed on first load.
- Installation is one command; the desktop copier moved to `scripts/legacy/` (Hermes older than 0.22).
- Private Hermes imports dropped from ten to one (the Nous Portal helper).

### Removed

- The plugin no longer reads the Claude CLI's account file, so the Claude plan label is gone.
- The plugin no longer identifies itself as Claude Code or Codex in request headers.
- The plugin no longer binds a Hermes secret scope; a request without one reports `missing_profile_scope`.

### Fixed

- A response that arrives after the profile or gateway route changed is dropped instead of being shown under the wrong profile.
- Opus / Sonnet weekly caps no longer pose as the account quota in the status bar, the summary or the alerts.
- Claude sessions at 1 % or less no longer show as 100 % used right after a 5-hour reset: utilization is read as the percent Anthropic returns (works around upstream Hermes #58226).
- Cached usage is discarded as soon as any window's reset time passes, so a rolled-over window is never shown from cache.
- Five-hour/current-session windows are prioritized, then weekly, monthly and other windows, in cards and the status chip, including saved provider selections.
- A present session window with unavailable usage stays honest instead of being replaced by weekly usage.
- Z.ai monthly MCP quotas are labelled only for the verified type/unit/number combination.

### Limitations

- Claude is still parsed by the plugin because Hermes core misreads values of 1 % or less (#58226); that block goes away once core is fixed.
- `availability.json` is refreshed while Hermes Desktop is open (it is written when the backend collects); otherwise it ages to `unknown`.
- Hermes 0.23 has no `usage_extra` hook, so `/usage` itself is unchanged; `/usage-availability` is the CLI and gateway equivalent.
- Kimi and Z.ai now need the key in the profile's environment; a key held only in a credential pool is not seen.
- The interface is in English.

## [1.3.2] - 2026-08-25

### Added

- Public English documentation and user guide
- Cross-platform desktop installer and uninstaller
- Offline provider contract tests
- Public safety scanner for secrets, personal paths, and identifiers
- GitHub Actions CI
- Shared sanitized Claude usage cache

### Changed

- Public UI and backend messages are in English
- Provider refreshes are cache-protected instead of forcibly bypassing cache
- Claude fallback badge is explicitly marked `STALE`
- Hermes cache location is resolved without hardcoded personal paths

### Fixed

- Repeated stale-provider messages no longer accumulate
- Temporary Anthropic HTTP 429 responses no longer remove the Claude usage card
- Missing data is no longer presented as a valid zero-usage value
