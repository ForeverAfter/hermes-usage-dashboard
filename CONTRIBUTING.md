# Contributing

Thank you for improving Hermes Usage Dashboard.

## Development setup

```bash
python -m pip install -r requirements-dev.txt
```

## Required checks

Run all checks before opening a pull request:

```bash
python scripts/security_scan.py
python -m pytest
python -m ruff check dashboard scripts tests
python -m compileall -q dashboard scripts tests
node --check desktop/plugin.js
```

## Pull request rules

- Keep changes focused and reversible.
- Never include credentials, account IDs, email addresses, private logs, or machine-specific paths.
- Add or update tests for behavior changes.
- Preserve honest unavailable and stale states; never invent missing quota windows.
- Keep provider requests in the backend.
- Do not expose raw provider responses to the frontend.
- Document provider-specific assumptions in `docs/PROVIDER_NOTES.md`.

## Provider integrations

When adding or changing a provider:

1. Use the provider's documented or verifiably observed usage fields.
2. Bound network timeouts.
3. Clamp percentages to the range 0–100.
4. Normalize reset times to ISO 8601 UTC.
5. Return static sanitized errors.
6. Add offline fixture tests.
7. Update privacy and provider documentation.

## Commit style

Use concise imperative commit messages. Do not add generated audit transcripts or local debug output to the repository.
