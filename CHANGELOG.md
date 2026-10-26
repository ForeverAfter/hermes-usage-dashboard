# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Fixed

- Claude sessions at 1 % or less no longer show as 100 % used right after a 5-hour reset: utilization is read as the percent Anthropic returns (works around upstream Hermes #58226).
- Cached usage is discarded as soon as any window's reset time passes, so a rolled-over window is never shown from cache.
- Hermes 0.22+ binds the launch profile's secret scope on plugin routes itself; the plugin now recognizes that core-bound launch scope and keeps its response cache, Anthropic cache, and Claude plan label instead of re-querying every provider on each poll. Routed (foreign-home) profiles still bypass launch caches.
- Preserve the Hermes profile secret scope and home context in parallel collectors.
- Establish the launch-profile scope for unscoped plugin requests without disabling fail-closed protection.
- Isolate launch-profile caches from requests with an existing secret scope.
- Prioritize five-hour/current-session windows, then weekly, monthly, and other windows in cards and the status chip, including saved provider selections.
- Keep a present session window with unavailable usage honest instead of substituting weekly usage.
- Use the current Hermes Codex backend URL helper.
- Label Z.ai monthly MCP quotas only for the verified type/unit/number combination.
- Recover Claude plan labels from allowlisted local metadata for launch-profile requests.

### Added

- Deep allowlists for backend responses and frontend cached payloads.
- A fixed, sanitized diagnostic category for missing profile scope.
- Optional plan-renewal display support, distinct from quota-window resets.
- Context propagation, isolation, restoration, and real-core offline regression tests.
- Executable Node tests for status-chip selection and window priority.

### Limitations

- Existing request scopes bypass launch caches, which may increase provider requests.
- The optional Kimi Desktop log fallback remains local to the operating-system user; complete cross-account isolation is not claimed.

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
