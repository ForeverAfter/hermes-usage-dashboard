# Security Policy

## Supported versions

Security fixes are provided for the latest published release.

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** private security advisory feature for this repository. Do not open a public issue for suspected credential exposure or authentication bypasses.

Include:

- A concise description
- Affected version
- Reproduction steps
- Expected and observed behavior
- Impact assessment
- A minimal sanitized proof of concept

Never include real tokens, cookies, authorization headers, account identifiers, or private logs.

## Security design

- Credentials are resolved and used only by the local Python backend.
- The frontend calls a plugin-scoped local route.
- Responses use an explicit sanitized schema.
- Exceptions are not serialized to the UI.
- Provider requests have bounded timeouts.
- Static CI scans reject common secret formats, personal filesystem paths, and email addresses.

## Threat model limitations

A process with full access to the user's Hermes home may already be able to read Hermes credentials. This plugin does not attempt to defend against a fully compromised local account or operating system.
