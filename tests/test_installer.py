from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_installer():
    spec = importlib.util.spec_from_file_location("install_desktop_test", ROOT / "scripts" / "install_desktop.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_desktop_installer_uses_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "custom-home"))
    installer = load_installer()
    target = installer.install()
    assert target == tmp_path / "custom-home" / "desktop-plugins" / "usage-dashboard" / "plugin.js"
    assert target.read_bytes() == (ROOT / "desktop" / "plugin.js").read_bytes()
