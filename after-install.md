# AI Usage is installed

That is all: Hermes Desktop loads the status-bar chip from the installed plugin. If the **AI** chip does not
appear in the status bar, run **Reload desktop plugins** from the command palette.

- The chip shows the quota of the provider your focused session is using. Click it for every provider.
- Agents can ask `usage_availability` (or you can type `/usage-availability`) which subscription still has quota.
- Kimi and Z.ai use the keys Hermes already has (`KIMI_CODING_API_KEY` / `KIMI_API_KEY`, `GLM_API_KEY` / `ZAI_API_KEY`).
- Settings (thresholds, alerts, optional Kimi Desktop log) are under **Settings → Plugins → AI Usage**.

Check the install with `hermes plugins doctor usage-dashboard`. See `README.md` for what the plugin reads and sends.
