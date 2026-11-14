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


def test_frontend_scopes_cache_and_responses_to_profile_connection_and_epoch():
    source = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert "QUERY_KEY" not in source  # no query or cache key without a profile in it
    assert "queryKey: [ID, 'usage', key, epoch]" in source
    assert "last-good-usage-v2" in source
    assert "typeof sdk.gatewayActivationEpoch === 'function'" in source  # the export only exists on Hermes >= 0.23
    assert source.count("scopeIsCurrent(scope)") >= 2
    assert "ctx.storage.remove(LEGACY_USAGE_CACHE_KEY)" in source


def test_frontend_stays_inside_the_plugin_sdk_surface():
    source = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    for forbidden in ("document.", "window.hermesDesktop", "eval(", "new Function", "prototype", "innerHTML"):
        assert forbidden not in source, forbidden
    assert source.count("from '@hermes/plugin-sdk'") == 2  # the named SDK imports and the optional-export namespace


def test_frontend_badges_distinguish_stale_and_local_sources():
    source = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    stale_position = source.index("provider.usage_fallback || provider.stale")
    local_position = source.index(": provider.local", stale_position)
    active_position = source.index(": provider.available", local_position)
    assert stale_position < local_position < active_position


def test_disclosure_texts_describe_the_real_behavior():
    """U8: pin = provider, the pin is app-wide, model caps only ever affect the chip."""
    read = lambda name: (ROOT / name).read_text(encoding="utf-8")  # noqa: E731
    readme, readme_es, guide, privacy, notes = (
        read("README.md"), read("README.es.md"), read("docs/USER_GUIDE.md"), read("PRIVACY.md"),
        read("docs/PROVIDER_NOTES.md"),
    )
    # 1. Pinning keeps the provider, not a window.
    assert "pinned window" not in readme
    assert "pin that provider and window" not in guide
    assert "ventana fijada" not in readme_es
    assert "pin its provider" in readme and "fijar su proveedor" in readme_es
    # 2. The pin is one app-wide preference; the caches and alert memory are per profile and connection.
    per_profile = privacy.split("In the Desktop plugin storage (per profile and connection):")[1].split("\n\n")[1]
    assert "pinned" not in per_profile and "last good" in per_profile
    for text in (readme, guide, privacy):
        assert "app-wide" in text
    assert "toda la app" in readme_es
    # 3. Model caps: only the status-bar chip, never the summary, alerts or availability.json.
    assert "unless the session runs" not in notes
    assert "only the status-bar chip" in notes


def test_alert_texts_describe_last_level_and_once_per_crossing():
    """N3: the memory holds the last observed level; alerts fire once per crossing and re-arm after a drop;
    homonymous sources may repeat a critical alert once per activation."""
    guide = (ROOT / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8")
    privacy = (ROOT / "PRIVACY.md").read_text(encoding="utf-8")
    assert "once per window" not in guide
    assert "highest alert level" not in privacy
    assert "once per crossing" in guide and "re-arms" in guide
    assert "once per activation" in guide
    assert "last alert level observed" in privacy and "once each time it is activated" in privacy


def test_cache_texts_only_promise_reset_invalidation_of_fresh_caches():
    """N4: a reset invalidates the fresh caches; the fallbacks may still show the previous window, marked stale."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_es = (ROOT / "README.es.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "a cache dies the moment its window resets" not in readme
    assert "una caché muere en cuanto se reinicia su ventana" not in readme_es
    assert "A cache is discarded as soon as one of its windows passes its reset time." not in architecture
    assert "invalidates the backend's fresh caches" in readme and "can still show the previous window" in readme
    assert "invalida las cachés frescas del backend" in readme_es and "ventana anterior" in readme_es
    assert "invalidates the FRESH caches only" in architecture and "not checked against reset times" in architecture
