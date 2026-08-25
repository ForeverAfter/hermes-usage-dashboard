# Privacy

Hermes Usage Dashboard is designed for local use and does not operate a remote service.

## Data accessed

Depending on the provider, the local backend may access:

- OAuth access tokens or API keys already configured in Hermes
- Subscription usage percentages and reset times
- Plan labels and credit balances
- Optional Kimi Desktop subscription log entries

## Data sent to provider services

The backend sends the credential required by each provider only to that provider's official usage endpoint. It does not send provider credentials to any third party.

## Data sent to the desktop frontend

Only sanitized display data is returned:

- Provider name
- Availability state
- Plan label
- Usage percentages
- Reset times
- Credit totals
- Static error explanations
- Provider-neutral source labels

The frontend does not receive raw credentials, authorization headers, email addresses, account IDs, OAuth claims, or raw API responses.

## Local storage

The plugin stores:

- The manually pinned status-bar selection
- A sanitized last-good usage payload, expiring after 24 hours
- A sanitized Claude usage cache used to reduce rate limiting

No secret is intentionally stored by this plugin.

## Telemetry

The plugin contains no telemetry, analytics, advertising, crash reporting, or remote logging.

## Deletion

Uninstall the plugin and remove its desktop UI using the documented uninstall command. Any remaining sanitized cache can be removed from the Hermes cache and plugin storage directories.
