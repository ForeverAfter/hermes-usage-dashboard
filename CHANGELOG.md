# Changelog

All notable changes to this project are documented here.

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
