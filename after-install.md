# Installation completed

The Python backend is installed and enabled.

Install the Hermes Desktop status-bar UI:

**Windows PowerShell**

```powershell
python "$env:LOCALAPPDATA\hermes\plugins\usage-dashboard\scripts\install_desktop.py"
```

**macOS or Linux**

```bash
python "${HERMES_HOME:-$HOME/.hermes}/plugins/usage-dashboard/scripts/install_desktop.py"
```

Then restart the Hermes gateway. If the status-bar indicator does not appear automatically, run **Reload desktop plugins** from the Hermes Desktop command palette.

Read `README.md` and `docs/USER_GUIDE.md` for provider setup and troubleshooting.
