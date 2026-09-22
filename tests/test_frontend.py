from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_frontend_selection_behavior_with_node():
    result = subprocess.run(
        ["node", "--test", str(ROOT / "tests" / "frontend_selection.cjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_frontend_parses_with_node():
    result = subprocess.run(
        ["node", "--check", str(ROOT / "desktop" / "plugin.js")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_frontend_uses_protected_refresh_and_deduplicated_fallback():
    source = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert "fetchUsage('/usage?force=true')" not in source
    assert "new Set([...baseDetails, reason])" in source
    assert "usage_fallback_reason" in source
    assert "provider.usage_fallback || provider.stale" in source
    assert ": provider.local" in source
    assert "fetched_at: payload?.fetched_at" in source


def test_frontend_deep_whitelists_provider_payloads():
    source = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert "function sanitizeProvider(provider)" in source
    assert ".map(sanitizeProvider)" in source
    assert "renewal_at:" in source


def test_frontend_displays_plan_renewal_cutoff():
    source = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert "formatDate(provider.renewal_at)" in source
    assert "Plan renewal / cutoff" in source


def test_frontend_badges_distinguish_stale_and_local_sources():
    source = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    stale_position = source.index("provider.usage_fallback || provider.stale")
    local_position = source.index(": provider.local", stale_position)
    active_position = source.index(": provider.available", local_position)
    assert stale_position < local_position < active_position
