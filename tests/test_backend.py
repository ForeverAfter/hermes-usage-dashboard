from __future__ import annotations

import importlib.util
import json
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    spec = importlib.util.spec_from_file_location("usage_dashboard_test_backend", ROOT / "dashboard" / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module._ANTHROPIC_CACHE_FILE = tmp_path / "anthropic.json"
    return module


def sample_snapshot():
    window = SimpleNamespace(
        label="Current session",
        used_percent=25.0,
        reset_at=datetime.now(timezone.utc) + timedelta(hours=5),
        detail=None,
    )
    return SimpleNamespace(
        unavailable_reason=None,
        windows=(window,),
        available=True,
        plan="Test plan",
        source="test-source",
        fetched_at=datetime.now(timezone.utc),
        details=(),
    )


def install_fake_account_usage(monkeypatch: pytest.MonkeyPatch, fetcher):
    agent_module = types.ModuleType("agent")
    usage_module = types.ModuleType("agent.account_usage")
    usage_module._fetch_anthropic_account_usage = fetcher
    agent_module.account_usage = usage_module
    monkeypatch.setitem(sys.modules, "agent", agent_module)
    monkeypatch.setitem(sys.modules, "agent.account_usage", usage_module)


def test_snapshot_card_clamps_percentages(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    snapshot = sample_snapshot()
    snapshot.windows[0].used_percent = 140.0
    card = backend._snapshot_to_card("test", "Test", snapshot)
    assert card["windows"][0]["used_percent"] == 100.0
    assert card["windows"][0]["remaining_percent"] == 0.0


def test_anthropic_429_keeps_last_valid_card(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    install_fake_account_usage(monkeypatch, sample_snapshot)
    first = backend._anthropic_card()
    assert first["available"] is True

    entry = json.loads(backend._ANTHROPIC_CACHE_FILE.read_text(encoding="utf-8"))
    entry["saved_at"] = time.time() - 600
    backend._ANTHROPIC_CACHE_FILE.write_text(json.dumps(entry), encoding="utf-8")

    def rate_limited():
        request = httpx.Request("GET", "https://api.anthropic.com/api/oauth/usage")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    install_fake_account_usage(monkeypatch, rate_limited)
    stale = backend._anthropic_card()
    assert stale["available"] is True
    assert stale["stale"] is True
    assert any("HTTP 429" in detail for detail in stale["details"])


def test_collect_contract_contains_no_credential_fields(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)

    def safe_card(provider: str):
        return {
            "provider": provider,
            "label": provider,
            "available": True,
            "plan": "Example",
            "source": "test",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "windows": [
                {
                    "label": "Window",
                    "used_percent": 10.0,
                    "remaining_percent": 90.0,
                    "reset_at": None,
                    "detail": None,
                }
            ],
            "details": [],
            "reason": None,
        }

    monkeypatch.setattr(backend, "_codex_card", lambda: safe_card("openai-codex"))
    monkeypatch.setattr(backend, "_anthropic_card", lambda: safe_card("anthropic"))
    monkeypatch.setattr(backend, "_kimi_card", lambda: safe_card("kimi"))
    monkeypatch.setattr(backend, "_zai_card", lambda: safe_card("zai"))
    monkeypatch.setattr(backend, "_nous_card", lambda: safe_card("nous"))
    payload = backend._collect()
    serialized = json.dumps(payload).lower()
    for forbidden in ("authorization", "api_key", "access_token", "refresh_token", "password", "email"):
        assert forbidden not in serialized
    assert payload["summary"]["available"] == 5


def test_zai_current_value_is_used_percentage(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)

    runtime_module = types.ModuleType("hermes_cli.runtime_provider")
    runtime_module.resolve_runtime_provider = (
        lambda requested: {"api_key": "test-placeholder"}  # pragma: allowlist secret
    )
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.runtime_provider",
        runtime_module,
    )

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            if url.endswith("/quota/limit"):
                return FakeResponse(
                    {
                        "data": {
                            "limits": [
                                {
                                    "usage": 100,
                                    "currentValue": 25,
                                    "remaining": 75,
                                    "type": "TOKENS_LIMIT",
                                    "unit": 3,
                                    "number": 5,
                                }
                            ]
                        }
                    }
                )
            return FakeResponse({"data": []})

    monkeypatch.setattr(backend.httpx, "Client", FakeClient)
    card = backend._zai_card()
    window = card["windows"][0]
    assert window["used_percent"] == 25.0
    assert window["remaining_percent"] == 75.0


def test_kimi_log_card_marks_fresh_local_data(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    log_file = tmp_path / "main.log"
    log_file.write_text(
        "[2026-08-25T11:30:00+00:00] refreshed(sub): level=2 "
        "isMember=true omniRatio=0.25 exhausted=false "
        "resetAt=2026-09-01T00:00:00Z\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(backend, "_kimi_log_file", lambda: log_file)
    monkeypatch.setattr(backend, "_now", lambda: now)
    card = backend._kimi_log_card()
    assert card["available"] is True
    assert card["local"] is True
    assert card["stale"] is False
    assert card["windows"][0]["remaining_percent"] == 75.0


def test_usage_route_has_no_force_bypass(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    assert backend.get_usage.__code__.co_argcount == 0


def test_nous_empty_snapshot_returns_explicit_unavailable_reason(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    snapshot = SimpleNamespace(
        windows=(),
        details=(),
        plan="Example",
        fetched_at=datetime.now(timezone.utc),
    )
    account_usage = types.ModuleType("agent.account_usage")
    account_usage.build_nous_credits_snapshot = lambda account: snapshot
    agent_module = types.ModuleType("agent")
    agent_module.account_usage = account_usage
    nous_account = types.ModuleType("hermes_cli.nous_account")
    nous_account.get_nous_portal_account_info = lambda force_fresh: {"ok": True}
    monkeypatch.setitem(sys.modules, "agent", agent_module)
    monkeypatch.setitem(sys.modules, "agent.account_usage", account_usage)
    monkeypatch.setitem(sys.modules, "hermes_cli.nous_account", nous_account)

    card = backend._nous_card()
    assert card["available"] is False
    assert card["reason"] == "Nous Portal did not return a usable credit balance."
