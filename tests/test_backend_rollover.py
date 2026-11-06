"""Claude session rollover: utilization scale and reset-aware caches (offline, no credentials)."""
import json
import time
from datetime import timedelta

from test_backend import install_anthropic, iso, load_backend, sample_snapshot


def test_fresh_session_utilization_at_or_below_one_is_a_percentage(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    install_anthropic(monkeypatch, backend, {
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
    assert "cc-synthetic-oauth" not in json.dumps(card)


def test_opus_and_sonnet_weekly_windows_are_model_scoped(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    install_anthropic(monkeypatch, backend, {
        "five_hour": {"utilization": 10.0, "resets_at": iso(timedelta(hours=4))},
        "seven_day": {"utilization": 20.0, "resets_at": iso(timedelta(days=2))},
        "seven_day_opus": {"utilization": 95.0, "resets_at": iso(timedelta(days=2))},
        "seven_day_sonnet": {"utilization": 90.0, "resets_at": iso(timedelta(days=2))},
    })
    scopes = {w["label"]: w["scope"] for w in backend._public_card(backend._anthropic_card())["windows"]}
    assert scopes == {
        "Current session": "account",
        "Current week": "account",
        "Opus week": "model",
        "Sonnet week": "model",
    }


def test_non_oauth_token_is_reported_without_a_request(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    calls = []
    install_anthropic(monkeypatch, backend, {}, token="sk-ant-api03-xyz", calls=calls)
    card = backend._anthropic_card()
    assert card["available"] is False
    assert "OAuth" in card["reason"]
    assert calls == []


def test_missing_token_is_reported_without_a_request(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    calls = []
    install_anthropic(monkeypatch, backend, {}, token="", calls=calls)
    assert backend._anthropic_card()["available"] is False
    assert calls == []


def test_oauth_token_shapes():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("usage_dashboard_oauth_shapes", path)
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    for token in ("sk-ant-oat01-abc", "eyJhbGciOi.payload.sig", "cc-abc", "sk-ant-new-x"):
        assert backend._is_oauth_token(token), token
    for token in ("sk-ant-api03-abc", "sk-ant-usr-abc", "sk-ant-svc-abc", "sk-ant-admin-abc", "plain", ""):
        assert not backend._is_oauth_token(token), token


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
    first = backend._get_usage()
    second = backend._get_usage()
    assert second is not first  # the cached window already rolled over
    assert backend._get_usage() is second  # a live window keeps the 120 s dedupe
    assert len(payloads) == 2
    assert time.monotonic() - backend._CACHE["at"] < backend._CACHE_TTL_SECONDS
