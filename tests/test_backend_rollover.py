"""Claude session rollover: utilization scale and reset-aware caches (offline, no credentials)."""
import json
import sys
import time
import types
from datetime import datetime, timedelta, timezone

from test_backend import load_backend, sample_snapshot

URL = "https://api.anthropic.com/api/oauth/usage"


def install_raw_anthropic_api(monkeypatch, payload, calls=None):
    """Mimic the Hermes core helpers the plugin reuses; the token is synthetic."""
    usage = types.ModuleType("agent.account_usage")

    def get_json(url, headers, *, timeout):
        assert url == URL
        assert headers["Authorization"] == "Bearer synthetic-oauth"
        if calls is not None:
            calls.append(url)
        return payload

    def parse_dt(value):
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None

    usage._get_json = get_json
    usage._parse_dt = parse_dt
    usage._is_oauth_token = lambda token: token == "synthetic-oauth"
    usage.resolve_anthropic_token = lambda: "synthetic-oauth"
    usage._fetch_anthropic_account_usage = lambda: (_ for _ in ()).throw(
        AssertionError("core fetch scales utilization <= 1 by 100 (#58226)"))
    agent = types.ModuleType("agent")
    agent.account_usage = usage
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.account_usage", usage)


def iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


def test_fresh_session_utilization_at_or_below_one_is_a_percentage(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    monkeypatch.setattr(backend, "_anthropic_plan_from_claude_config", lambda: None)
    install_raw_anthropic_api(monkeypatch, {
        "five_hour": {"utilization": 1.0, "resets_at": iso(timedelta(hours=4, minutes=46))},
        "seven_day": {"utilization": 32.0, "resets_at": iso(timedelta(days=2))},
        "seven_day_opus": {"utilization": 0.5, "resets_at": iso(timedelta(days=2))},
    })
    card = backend._anthropic_card()
    windows = {w["label"]: w for w in card["windows"]}
    assert card["available"] is True
    assert windows["Current session"]["used_percent"] == 1.0
    assert windows["Current session"]["remaining_percent"] == 99.0
    assert windows["Current week"]["used_percent"] == 32.0
    assert windows["Opus week"]["used_percent"] == 0.5
    assert "synthetic-oauth" not in json.dumps(card)


def test_non_oauth_token_is_reported_without_a_request(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    calls = []
    install_raw_anthropic_api(monkeypatch, {}, calls)
    sys.modules["agent.account_usage"].resolve_anthropic_token = lambda: "plain-api-key"
    card = backend._anthropic_card()
    assert card["available"] is False
    assert "OAuth" in card["reason"]
    assert calls == []


def test_fresh_cache_is_ignored_once_a_window_has_reset(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    card = backend._snapshot_to_card("anthropic", "Claude Code", sample_snapshot())
    card["windows"][0]["used_percent"] = 100.0
    card["windows"][0]["reset_at"] = iso(-timedelta(minutes=1))
    backend._write_anthropic_cache(card)
    assert backend._read_anthropic_cache(backend._ANTHROPIC_FRESH_SECONDS) is None
    # The stale fallback may still show it (marked STALE) when the provider is down.
    assert backend._read_anthropic_cache(backend._ANTHROPIC_STALE_SECONDS) is not None


def test_response_cache_expires_when_any_window_resets(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    payloads = []

    def collect():
        reset = iso(timedelta(seconds=-1)) if not payloads else iso(timedelta(hours=5))
        payload = {"providers": [{"windows": [{"label": "Current session", "reset_at": reset}]}]}
        payloads.append(payload)
        return payload

    monkeypatch.setattr(backend, "_collect", collect)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", None)
    first = backend.get_usage()
    second = backend.get_usage()
    assert second is not first  # the cached window already rolled over
    assert backend.get_usage() is second  # a live window keeps the 120 s dedupe
    assert len(payloads) == 2
    assert time.monotonic() - backend._CACHE["at"] < backend._CACHE_TTL_SECONDS
