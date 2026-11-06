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
ANTHROPIC_URL = "https://api.anthropic.com/api/oauth/usage"


def load_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **settings):
    """Load plugin_api.py hermetically: tmp data dir, fixed settings, no real Hermes config."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    spec = importlib.util.spec_from_file_location("usage_dashboard_test_backend", ROOT / "dashboard" / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    data = tmp_path / "plugin-data"
    data.mkdir(exist_ok=True)
    module._data_dir = lambda: data
    merged = {**module._DEFAULT_SETTINGS, **settings}
    module._plugin_settings = lambda: dict(merged)
    return module


def sample_snapshot():
    window = SimpleNamespace(
        label="Current session",
        used_percent=25.0,
        reset_at=datetime.now(timezone.utc) + timedelta(hours=5),
        detail=None,
        scope="account",
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


class FakeResponse:
    def __init__(self, payload=None, status=200, url="https://example.invalid/"):
        self._payload = payload if payload is not None else {}
        self._status = status
        self._request = httpx.Request("GET", url)

    def raise_for_status(self):
        if self._status >= 400:
            raise httpx.HTTPStatusError(
                "http error", request=self._request, response=httpx.Response(self._status, request=self._request)
            )

    def json(self):
        return self._payload


def fake_http(monkeypatch, backend, routes, calls=None):
    """Replace httpx.Client: ``routes`` maps a URL suffix to a payload, an int status or an Exception."""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None, **kwargs):
            if calls is not None:
                calls.append({"url": url, "headers": dict(headers or {})})
            for suffix, outcome in routes.items():
                if url.endswith(suffix):
                    if isinstance(outcome, Exception):
                        raise outcome
                    if isinstance(outcome, int):
                        return FakeResponse(status=outcome, url=url)
                    return FakeResponse(outcome, url=url)
            raise AssertionError(f"unexpected request: {url}")

    monkeypatch.setattr(backend.httpx, "Client", FakeClient)


def install_secrets(monkeypatch, values=None):
    """Fake ``agent.secret_scope`` so no real Hermes core (or credential) is needed."""
    values = values or {}
    module = types.ModuleType("agent.secret_scope")

    class UnscopedSecretError(RuntimeError):
        pass

    module.UnscopedSecretError = UnscopedSecretError
    module.get_secret = lambda name, default=None: values.get(name, default)
    module.current_secret_scope = lambda: {}
    agent = sys.modules.get("agent") or types.ModuleType("agent")
    if not isinstance(agent, types.ModuleType):
        agent = types.ModuleType("agent")
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", module)
    return module


def install_account_usage(monkeypatch, **attributes):
    """Fake ``agent.account_usage`` exposing only the given attributes (so private imports fail)."""
    module = types.ModuleType("agent.account_usage")
    for name, value in attributes.items():
        setattr(module, name, value)
    agent = types.ModuleType("agent")
    agent.account_usage = module
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.account_usage", module)
    return module


def install_anthropic(monkeypatch, backend, outcome, token="cc-synthetic-oauth", calls=None):
    install_account_usage(monkeypatch, resolve_anthropic_token=lambda: token)
    fake_http(monkeypatch, backend, {ANTHROPIC_URL: outcome}, calls)


def iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


def test_snapshot_card_clamps_percentages(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    snapshot = sample_snapshot()
    snapshot.windows[0].used_percent = 140.0
    card = backend._snapshot_to_card("test", "Test", snapshot)
    assert card["windows"][0]["used_percent"] == 100.0
    assert card["windows"][0]["remaining_percent"] == 0.0


def test_snapshot_card_keeps_model_scope(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    snapshot = sample_snapshot()
    snapshot.windows[0].scope = "model"
    card = backend._public_card(backend._snapshot_to_card("anthropic", "Claude", snapshot))
    assert card["windows"][0]["scope"] == "model"
    legacy = SimpleNamespace(**{key: value for key, value in vars(snapshot.windows[0]).items() if key != "scope"})
    snapshot.windows = (legacy,)
    assert backend._snapshot_to_card("x", "X", snapshot)["windows"][0]["scope"] == "account"


def test_anthropic_429_keeps_last_valid_card(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    install_anthropic(monkeypatch, backend, {"five_hour": {"utilization": 25.0, "resets_at": iso(timedelta(hours=5))}})
    first = backend._anthropic_card()
    assert first["available"] is True

    cache = backend._anthropic_cache_path()
    entry = json.loads(cache.read_text(encoding="utf-8"))
    entry["saved_at"] = time.time() - 600
    cache.write_text(json.dumps(entry), encoding="utf-8")

    install_anthropic(monkeypatch, backend, 429)
    stale = backend._anthropic_card()
    assert stale["available"] is True
    assert stale["stale"] is True
    assert any("HTTP 429" in detail for detail in stale["details"])


def test_anthropic_cache_lives_in_plugin_data_dir(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    install_anthropic(monkeypatch, backend, {"five_hour": {"utilization": 25.0, "resets_at": iso(timedelta(hours=5))}})
    backend._anthropic_card()
    assert (tmp_path / "plugin-data" / "anthropic-cache.json").is_file()
    assert not (tmp_path / "hermes" / "cache").exists()


def test_anthropic_never_reads_claude_cli_account_file(tmp_path, monkeypatch):
    # ~/.claude.json belongs to another tool's login: the plugin must not open it, with or without data.
    (tmp_path / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"organizationType": "claude_max"}}), encoding="utf-8"
    )
    backend = load_backend(tmp_path, monkeypatch)
    install_anthropic(monkeypatch, backend, {"five_hour": {"utilization": 25.0, "resets_at": iso(timedelta(hours=5))}})
    opened = []
    real_read_text = Path.read_text

    def spy(self, *args, **kwargs):
        opened.append(self.name)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    card = backend._anthropic_card()
    assert card["plan"] is None
    assert ".claude.json" not in opened
    assert ".claude.json" not in (ROOT / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")
    assert not hasattr(backend, "_anthropic_plan_from_claude_config")


def test_anthropic_request_does_not_pose_as_another_vendors_client(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    calls = []
    install_anthropic(monkeypatch, backend, {}, calls=calls)
    backend._anthropic_card()
    headers = calls[0]["headers"]
    assert headers["User-Agent"].startswith("hermes-usage-dashboard/")
    assert "claude" not in headers["User-Agent"].lower()
    assert calls[0]["url"] == ANTHROPIC_URL


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
                    "authorization": "redacted",
                }
            ],
            "details": [],
            "reason": None,
            "metadata": {"access_token": "redacted"},
        }

    monkeypatch.setattr(backend, "_codex_card", lambda: safe_card("openai-codex"))
    monkeypatch.setattr(backend, "_anthropic_card", lambda: safe_card("anthropic"))
    monkeypatch.setattr(backend, "_kimi_card", lambda: safe_card("kimi"))
    monkeypatch.setattr(backend, "_zai_card", lambda: safe_card("zai"))
    monkeypatch.setattr(backend, "_nous_card", lambda: safe_card("nous"))
    payload = backend._collect()
    serialized = json.dumps(payload).lower()
    for forbidden in ("authorization", "bearer", "api_key", "access_token", "refresh_token", "password", "email"):
        assert forbidden not in serialized
    assert payload["summary"]["available"] == 5


def test_collect_orders_every_provider_five_hour_weekly_monthly(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)

    def scrambled_card(provider: str):
        return {
            "provider": provider,
            "label": provider,
            "available": True,
            "plan": "Example",
            "source": "test",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "windows": [
                {"label": "Monthly period", "remaining_percent": 80.0},
                {"label": "Current week", "remaining_percent": 70.0},
                {"label": "Current session", "remaining_percent": 60.0},
            ],
            "details": [],
            "reason": None,
        }

    monkeypatch.setattr(backend, "_codex_card", lambda: scrambled_card("openai-codex"))
    monkeypatch.setattr(backend, "_anthropic_card", lambda: scrambled_card("anthropic"))
    monkeypatch.setattr(backend, "_kimi_card", lambda: scrambled_card("kimi"))
    monkeypatch.setattr(backend, "_zai_card", lambda: scrambled_card("zai"))
    monkeypatch.setattr(backend, "_nous_card", lambda: scrambled_card("nous"))

    payload = backend._collect()
    for card in payload["providers"]:
        assert [window["label"] for window in card["windows"]] == [
            "Current session",
            "Current week",
            "Monthly period",
        ]


def test_model_scoped_windows_stay_out_of_the_summary(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    card = {
        "provider": "anthropic",
        "label": "Claude Code",
        "available": True,
        "source": "test",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "windows": [
            {"label": "Current session", "remaining_percent": 60.0},
            {"label": "Opus week", "remaining_percent": 5.0, "scope": "model"},
        ],
    }
    for name in ("_codex_card", "_kimi_card", "_zai_card", "_nous_card"):
        monkeypatch.setattr(backend, name, lambda: backend._unavailable("x", "X", "offline"))
    monkeypatch.setattr(backend, "_anthropic_card", lambda: card)
    payload = backend._collect()
    assert payload["summary"]["lowest_remaining_percent"] == 60.0
    opus = next(w for w in payload["providers"][1]["windows"] if w["label"] == "Opus week")
    assert opus["scope"] == "model"


def test_window_order_recognizes_current_hermes_labels(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    windows = [
        {"label": "Monthly credits"},
        {"label": "Session"},
        {"label": "Weekly"},
    ]
    ordered = sorted(windows, key=backend._window_order)
    assert [window["label"] for window in ordered] == [
        "Session",
        "Weekly",
        "Monthly credits",
    ]


def test_codex_uses_hermes_public_fetcher_read_only(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    seen = []
    snapshot = sample_snapshot()
    snapshot.windows = (
        SimpleNamespace(label="Weekly", used_percent=40.0, reset_at=None, detail=None, scope="account"),
        SimpleNamespace(label="Session", used_percent=20.0, reset_at=None, detail=None, scope="account"),
    )
    snapshot.source = "usage_api"

    def fetch(provider, **kwargs):
        seen.append((provider, kwargs))
        return snapshot

    # Only the public fetcher exists on this fake module: any private Codex helper import would fail.
    install_account_usage(monkeypatch, fetch_account_usage=fetch)
    fake_http(monkeypatch, backend, {})  # the plugin makes no HTTP request of its own for Codex
    card = backend._codex_card()
    assert seen == [("openai-codex", {"read_only": True})]
    assert card["available"] is True
    assert card["source"] == "usage_api"
    assert [w["label"] for w in sorted(card["windows"], key=backend._window_order)] == ["Session", "Weekly"]


def test_codex_failure_is_a_fixed_unavailable_card(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)

    def fetch(provider, **kwargs):
        raise RuntimeError("secret detail that must not be shown")

    install_account_usage(monkeypatch, fetch_account_usage=fetch)
    card = backend._codex_card()
    assert card["available"] is False
    assert "secret detail" not in json.dumps(card)


def test_codex_without_session_reports_no_data(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    install_account_usage(monkeypatch, fetch_account_usage=lambda provider, **kwargs: None)
    assert backend._codex_card()["available"] is False


def zai_with(monkeypatch, backend, limits, key="test-placeholder"):
    install_secrets(monkeypatch, {"GLM_API_KEY": key} if key else {})
    fake_http(
        monkeypatch,
        backend,
        {"/quota/limit": {"data": {"limits": limits}}, "/subscription/list": {"data": []}},
    )


def test_zai_current_value_is_used_percentage(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    zai_with(
        monkeypatch,
        backend,
        [{"usage": 100, "currentValue": 25, "remaining": 75, "type": "TOKENS_LIMIT", "unit": 3, "number": 5}],
    )
    window = backend._zai_card()["windows"][0]
    assert window["used_percent"] == 25.0
    assert window["remaining_percent"] == 75.0


def test_zai_time_limit_is_labeled_as_monthly_mcp_quota(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    zai_with(
        monkeypatch,
        backend,
        [{"type": "TIME_LIMIT", "unit": 5, "number": 1, "usage": 100, "currentValue": 0, "remaining": 100,
          "percentage": 0}],
    )
    assert backend._zai_card()["windows"][0]["label"] == "Monthly MCP quota"


def test_zai_unverified_time_limit_stays_generic(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    zai_with(monkeypatch, backend, [{"type": "TIME_LIMIT", "unit": 4, "number": 1, "percentage": 20}])
    assert backend._zai_card()["windows"][0]["label"] == "Plan limit (1/4)"


def test_zai_percentage_only_exhaustion_is_explained(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    zai_with(monkeypatch, backend, [{"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 100}])
    assert backend._zai_card()["windows"][0]["detail"] == "Z.ai reports this quota as fully used"


def test_zai_and_kimi_keys_come_from_the_secret_scope_only(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    calls = []
    install_secrets(monkeypatch, {"ZAI_API_KEY": "zai-scoped-key", "KIMI_API_KEY": "kimi-scoped-key"})
    fake_http(
        monkeypatch,
        backend,
        {"/quota/limit": {"data": {"limits": []}}, "/subscription/list": {"data": []}, "/usages": {}},
        calls,
    )
    backend._zai_card()
    backend._kimi_card()
    sent = {call["url"]: call["headers"].get("Authorization") for call in calls}
    assert sent["https://api.z.ai/api/monitor/usage/quota/limit"] == "Bearer zai-scoped-key"
    assert sent["https://api.kimi.com/coding/v1/usages"] == "Bearer kimi-scoped-key"


def test_zai_without_a_key_makes_no_request(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    calls = []
    install_secrets(monkeypatch, {})
    fake_http(monkeypatch, backend, {}, calls)
    card = backend._zai_card()
    assert card["available"] is False
    assert calls == []


def test_unscoped_secret_read_reports_missing_profile_scope(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    secrets = install_secrets(monkeypatch, {})

    def refuse(name, default=None):
        raise secrets.UnscopedSecretError("private detail")

    secrets.get_secret = refuse
    for card in (backend._zai_card(), backend._kimi_card()):
        assert card["failure_category"] == "missing_profile_scope"
        assert "private detail" not in json.dumps(card)


KIMI_LOG = (
    "[2026-08-25T11:30:00+00:00] refreshed(sub): level=2 "
    "isMember=true omniRatio=0.25 exhausted=false "
    "resetAt=2026-09-01T00:00:00Z\n"
)


def test_kimi_log_card_marks_fresh_local_data(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch, kimi_desktop_log_fallback=True)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    log_file = tmp_path / "main.log"
    log_file.write_text(KIMI_LOG, encoding="utf-8")
    monkeypatch.setattr(backend, "_kimi_log_file", lambda: log_file)
    monkeypatch.setattr(backend, "_now", lambda: now)
    card = backend._kimi_log_card()
    assert card["available"] is True
    assert card["local"] is True
    assert card["stale"] is False
    assert card["windows"][0]["remaining_percent"] == 75.0


def test_kimi_log_is_never_opened_unless_the_setting_is_on(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)  # default: off
    log_file = tmp_path / "main.log"
    log_file.write_text(KIMI_LOG, encoding="utf-8")
    monkeypatch.setattr(backend, "_kimi_log_file", lambda: pytest.fail("the log path must not even be resolved"))
    install_secrets(monkeypatch, {})  # no key either
    card = backend._kimi_card()
    assert card["available"] is False
    assert card["source"] != "kimi-desktop-log"
    assert "settings" in card["reason"]


def test_kimi_api_failure_falls_back_to_the_log_only_when_enabled(tmp_path, monkeypatch):
    log_file = tmp_path / "main.log"
    log_file.write_text(KIMI_LOG, encoding="utf-8")
    for enabled in (True, False):
        backend = load_backend(tmp_path, monkeypatch, kimi_desktop_log_fallback=enabled)
        monkeypatch.setattr(backend, "_kimi_log_file", lambda: log_file)
        install_secrets(monkeypatch, {"KIMI_API_KEY": "kimi-scoped-key"})
        fake_http(monkeypatch, backend, {"/usages": 503})
        card = backend._kimi_card()
        assert (card["source"] == "kimi-desktop-log") is enabled


def test_plugin_settings_validate_types_and_ranges(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("usage_dashboard_settings", ROOT / "dashboard" / "plugin_api.py")
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)

    def configure(settings):
        config = types.ModuleType("hermes_cli.config")
        config.load_config_readonly = lambda: {"plugins": {"entries": {"usage-dashboard": {"settings": settings}}}}
        monkeypatch.setitem(sys.modules, "hermes_cli", types.ModuleType("hermes_cli"))
        monkeypatch.setitem(sys.modules, "hermes_cli.config", config)

    configure({"low_threshold_percent": 30, "alerts_enabled": False, "kimi_desktop_log_fallback": True})
    settings = backend._plugin_settings()
    assert settings["low_threshold_percent"] == 30
    assert settings["alerts_enabled"] is False
    assert settings["kimi_desktop_log_fallback"] is True
    configure({"low_threshold_percent": "high", "alerts_enabled": "yes", "alert_warn_percent": 500})
    assert backend._plugin_settings() == backend._DEFAULT_SETTINGS
    configure({"alert_warn_percent": 90, "alert_critical_percent": 85})  # critical must exceed warning
    settings = backend._plugin_settings()
    assert (settings["alert_warn_percent"], settings["alert_critical_percent"]) == (80, 95)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", None)  # core unreadable: defaults
    assert backend._plugin_settings() == backend._DEFAULT_SETTINGS


def test_usage_route_has_no_force_bypass(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    assert backend.get_usage.__code__.co_argcount == 0


def test_plan_renewal_is_extracted_from_provider_details(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    assert backend._renewal_from_details(["Renews: 2026-10-13T06:17:01.000Z"]) == (
        "2026-10-13T06:17:01+00:00"
    )


def test_nous_empty_snapshot_returns_explicit_unavailable_reason(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    snapshot = SimpleNamespace(
        windows=(),
        details=(),
        plan="Example",
        fetched_at=datetime.now(timezone.utc),
    )
    install_account_usage(monkeypatch, build_nous_credits_snapshot=lambda account: snapshot)
    nous_account = types.ModuleType("hermes_cli.nous_account")
    nous_account.get_nous_portal_account_info = lambda force_fresh: {"ok": True}
    monkeypatch.setitem(sys.modules, "hermes_cli", types.ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.nous_account", nous_account)

    card = backend._nous_card()
    assert card["available"] is False
    assert card["reason"] == "Nous Portal did not return a usable credit balance."
