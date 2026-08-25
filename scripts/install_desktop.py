"""Install the desktop UI portion of Hermes Usage Dashboard."""
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
    root = Path(__file__).resolve().parents[1]
    source = root / "desktop" / "plugin.js"
    if not source.is_file():
        raise FileNotFoundError(f"Desktop plugin source not found: {source}")
    target = hermes_home() / "desktop-plugins" / PLUGIN_ID / "plugin.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


if __name__ == "__main__":
    print(f"Desktop plugin installed at: {install()}")
