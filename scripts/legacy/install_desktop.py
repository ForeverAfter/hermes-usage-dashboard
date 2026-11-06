"""LEGACY (Hermes < 0.22): copy the desktop UI into ``desktop-plugins/``.

Hermes >= 0.22.5 loads ``desktop/plugin.js`` straight from the installed plugin, so do not run this
there: a second copy with the same id goes stale after ``hermes plugins update``.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

PLUGIN_ID = "usage-dashboard"


def hermes_home() -> Path:
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / "hermes"
        return Path.home() / "AppData" / "Local" / "hermes"
    return Path.home() / ".hermes"


def install() -> Path:
    root = Path(__file__).resolve().parents[2]
    source = root / "desktop" / "plugin.js"
    if not source.is_file():
        raise FileNotFoundError(f"Desktop plugin source not found: {source}")
    target = hermes_home() / "desktop-plugins" / PLUGIN_ID / "plugin.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


if __name__ == "__main__":
    print(f"Desktop plugin installed at: {install()}")
