"""Remove the desktop UI portion of Hermes Usage Dashboard."""
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


def uninstall() -> Path:
    directory = hermes_home() / "desktop-plugins" / PLUGIN_ID
    if directory.exists():
        shutil.rmtree(directory)
    return directory


if __name__ == "__main__":
    print(f"Desktop plugin removed from: {uninstall()}")
