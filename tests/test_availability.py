"""Contract 1: ``availability.json`` (writer in the backend, reader in the agent tool)."""
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_backend import (
    ANTHROPIC_URL,
    fake_http,
    install_account_usage,
    install_secrets,
    iso,
    load_backend,
    sample_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)


def card(*windows, provider="anthropic", fetched=NOW, available=True, stale=False):
    return {
        "provider": provider,
        "label": provider,
        "available": available,
        "stale": stale,
        "fetched_at": fetched.isoformat(),
        "windows": [
            {"label": label, "remaining_percent": remaining, "reset_at": reset, "scope": scope}
            for label, remaining, reset, scope in windows
        ],
    }


def entry(backend, *args, now=NOW, **kwargs):
    return backend._availability_entry(card(*args, **kwargs), backend._plugin_settings(), now)


def test_ok_low_and_exhausted_states(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    reset = "2026-11-06T15:00:00+00:00"
    ok = entry(backend, ("Current session", 77.0, reset, "account"), ("Current week", 90.0, None, "account"))
    assert ok == {"state": "ok", "remaining_percent": 77, "window": "session",
                  "reset_at": "2026-11-06T15:00:00Z", "stale": False}
    low = entry(backend, ("Current session", 80.0, reset, "account"), ("Current week", 9.0, reset, "account"))
    assert (low["state"], low["remaining_percent"], low["window"]) == ("low", 9, "week")
    exhausted = entry(backend, ("Current session", 0.0, reset, "account"), ("Current week", 50.0, None, "account"))
    assert (exhausted["state"], exhausted["remaining_percent"], exhausted["window"]) == ("exhausted", 0, "session")


def test_low_threshold_is_configurable_and_boundaries_are_inclusive(tmp_path, monkeypatch):
    default = load_backend(tmp_path, monkeypatch)
    assert entry(default, ("Current session", 15.0, None, "account"))["state"] == "low"
    assert entry(default, ("Current session", 15.4, None, "account"))["state"] == "ok"
    assert entry(default, ("Current session", 1.0, None, "account"))["state"] == "exhausted"
    assert entry(default, ("Current session", 1.4, None, "account"))["state"] == "low"
    strict = load_backend(tmp_path, monkeypatch, low_threshold_percent=40)
    assert entry(strict, ("Current session", 35.0, None, "account"))["state"] == "low"


def test_exhausted_provider_reports_when_access_actually_returns(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    soon, later = "2026-11-06T15:00:00+00:00", "2026-11-10T00:00:00+00:00"
    both = entry(backend, ("Current session", 0.0, soon, "account"), ("Current week", 0.0, later, "account"))
    assert (both["window"], both["reset_at"]) == ("week", "2026-11-10T00:00:00Z")
    unknown_reset = entry(backend, ("Current session", 0.0, soon, "account"), ("Current week", 0.0, None, "account"))
    assert (unknown_reset["window"], unknown_reset["reset_at"]) == ("week", None)


def test_model_scoped_windows_never_decide_the_state(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    result = entry(
        backend,
        ("Current session", 60.0, None, "account"),
        ("Opus week", 0.0, None, "model"),
        ("Sonnet week", 3.0, None, "model"),
    )
    assert (result["state"], result["remaining_percent"]) == ("ok", 60)
    only_model = entry(backend, ("Opus week", 0.0, None, "model"))
    assert only_model["state"] == "unknown"


def test_providers_without_session_or_week_windows_use_what_they_have(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    nous = entry(backend, ("Monthly credits", 40.0, None, "account"))
    assert (nous["state"], nous["window"], nous["remaining_percent"]) == ("ok", "month", 40)
    mcp_does_not_hide_the_session = entry(
        backend, ("5-hour window", 50.0, None, "account"), ("Monthly MCP quota", 0.0, None, "account")
    )
    assert mcp_does_not_hide_the_session["state"] == "ok"


def test_backend_and_chip_share_the_parity_fixture(tmp_path, monkeypatch):
    """N1: tests/frontend_selection.cjs runs the same windows through the chip's priorityOption."""
    backend = load_backend(tmp_path, monkeypatch)
    parity = json.loads((ROOT / "tests" / "fixtures" / "availability_parity.json").read_text(encoding="utf-8"))
    assert len(parity["cases"]) >= 7
    for case in parity["cases"]:
        for windows in (case["windows"], list(reversed(case["windows"]))):
            account_windows = [(label, remaining, None, "account") for label, remaining in windows]
            result = entry(backend, *account_windows, provider="zai")
            expected = (case["backend"]["state"], case["backend"]["window"])
            assert (result["state"], result["window"]) == expected, case["name"]
            if result["state"] == "exhausted":
                assert case["chip"]["exhausted"] is True, case["name"]


def test_unknown_when_missing_old_stale_or_unavailable(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    expected = {"state": "unknown", "remaining_percent": None, "window": None, "reset_at": None, "stale": True}
    assert backend._availability_entry(None, backend._plugin_settings(), NOW) == expected
    assert entry(backend, ("Current session", 50.0, None, "account"), available=False) == expected
    assert entry(backend, ("Current session", 50.0, None, "account"), stale=True) == expected
    old = NOW - timedelta(minutes=16)
    assert entry(backend, ("Current session", 50.0, None, "account"), fetched=old) == expected
    fresh = NOW - timedelta(minutes=14)
    assert entry(backend, ("Current session", 50.0, None, "account"), fetched=fresh)["state"] == "ok"
    no_percentage = entry(backend, ("Current session", None, None, "account"))
    assert no_percentage["state"] == "unknown" and no_percentage["stale"] is False


def test_document_has_the_exact_contract_shape(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    cards = [card(("Current session", 77.0, "2026-11-06T15:00:00+00:00", "account"), provider="anthropic")]
    document = backend._availability_document(cards, backend._plugin_settings(), NOW)
    assert list(document) == ["schema", "generated_at", "providers"]
    assert document["schema"] == 1
    assert document["generated_at"] == "2026-11-06T12:00:00Z"
    assert set(document["providers"]) == {"anthropic", "openai-codex", "kimi-coding", "zai", "nous"}
    for value in document["providers"].values():
        assert list(value) == ["state", "remaining_percent", "window", "reset_at", "stale"]
        assert value["state"] in {"ok", "low", "exhausted", "unknown"}
    assert document["providers"]["anthropic"]["state"] == "ok"
    assert document["providers"]["kimi-coding"]["state"] == "unknown"  # card id "kimi" maps to Hermes' "kimi-coding"
    kimi = backend._availability_document(
        [card(("Weekly quota", 9.0, None, "account"), provider="kimi")], backend._plugin_settings(), NOW
    )
    assert kimi["providers"]["kimi-coding"]["state"] == "low"


def load_plugin_package(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "usage_dashboard_pkg", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "usage_dashboard_pkg", module)
    spec.loader.exec_module(module)
    monkeypatch.delitem(sys.modules, "usage_dashboard_pkg.availability", raising=False)
    module.availability = importlib.import_module("usage_dashboard_pkg.availability")  # imported lazily by the plugin
    return module


def test_reader_round_trips_the_writers_file(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    plugin = load_plugin_package(monkeypatch)
    cards = [
        card(("Current session", 0.0, "2026-11-06T15:00:00+00:00", "account"), provider="openai-codex"),
        card(("Current session", 77.0, "2026-11-06T15:50:00+00:00", "account"), provider="anthropic"),
    ]
    backend._write_availability(backend._availability_document(cards, backend._plugin_settings(), NOW))
    path = tmp_path / "plugin-data" / "availability.json"
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 1
    document = plugin.availability.load(path, now=NOW + timedelta(minutes=2))
    assert document["providers"]["openai-codex"]["state"] == "exhausted"
    assert document["providers"]["anthropic"]["remaining_percent"] == 77
    summary = plugin.availability.format_summary(document, now=NOW + timedelta(minutes=2))
    assert "openai-codex: EXHAUSTED - 0% left (session), resets 2026-11-06T15:00:00Z (in 2h 58m)" in summary
    assert "anthropic: ok - 77% left" in summary
    assert "zai: unknown" in summary


def test_reader_treats_missing_corrupt_or_old_data_as_unknown(tmp_path, monkeypatch):
    plugin = load_plugin_package(monkeypatch)
    load = plugin.availability.load
    missing = load(tmp_path / "nope.json", now=NOW)
    assert all(v["state"] == "unknown" and v["stale"] for v in missing["providers"].values())
    assert "No quota data yet" in plugin.availability.format_summary(missing, now=NOW)
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert load(corrupt, now=NOW)["generated_at"] is None
    wrong_schema = tmp_path / "wrong.json"
    wrong_schema.write_text(json.dumps({"schema": 2, "generated_at": "2026-11-06T12:00:00Z"}), encoding="utf-8")
    assert load(wrong_schema, now=NOW)["generated_at"] is None
    old = tmp_path / "old.json"
    old.write_text(json.dumps({
        "schema": 1,
        "generated_at": "2026-11-06T11:00:00Z",
        "providers": {"anthropic": {"state": "ok", "remaining_percent": 90, "window": "session",
                                    "reset_at": None, "stale": False}},
    }), encoding="utf-8")
    stale = load(old, now=NOW)
    assert stale["providers"]["anthropic"]["state"] == "unknown"
    assert "older than 15 minutes" in plugin.availability.format_summary(stale, now=NOW)


def test_deeply_nested_json_reads_as_unknown_in_the_reader_and_both_handlers(tmp_path, monkeypatch):
    """N2: a RecursionError while decoding is a corrupt file, not an exception out of the reader."""
    plugin = load_plugin_package(monkeypatch)
    depth = 200_000  # far beyond any interpreter recursion limit
    nested = tmp_path / "nested.json"
    nested.write_text(
        '{"schema": 1, "generated_at": "2026-11-06T12:00:00Z", "extra": ' + "[" * depth + "0" + "]" * depth + "}",
        encoding="utf-8",
    )
    with pytest.raises(RecursionError):
        json.loads(nested.read_text(encoding="utf-8"))  # the scenario is real on this interpreter
    document = plugin.availability.load(nested, now=NOW)
    assert document["generated_at"] is None and document["age_seconds"] is None
    assert all(v["state"] == "unknown" and v["stale"] for v in document["providers"].values())

    registered = {}

    class Ctx:
        def register_tool(self, **kwargs):
            registered["tool"] = kwargs

        def register_command(self, name, handler, **kwargs):
            registered["command"] = handler

    plugin.register(Ctx())
    monkeypatch.setattr(plugin.availability, "availability_path", lambda: nested)
    output = json.loads(registered["tool"]["handler"]({}))
    assert "error" not in output
    assert "No quota data yet" in output["summary"]
    assert all(v["state"] == "unknown" for v in output["providers"].values())
    command = registered["command"]("")
    assert "No quota data yet" in command
    assert "could not be read" not in command


def test_reader_treats_a_future_timestamp_as_unknown(tmp_path, monkeypatch):
    """U5: a file stamped ahead of the clock (clock corrected later, or a stray writer) must not stay fresh."""
    plugin = load_plugin_package(monkeypatch)
    path = tmp_path / "availability.json"

    def write(generated_at):
        path.write_text(json.dumps({
            "schema": 1,
            "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "providers": {"anthropic": {"state": "exhausted", "remaining_percent": 0, "window": "week",
                                        "reset_at": None, "stale": False}},
        }), encoding="utf-8")
        return plugin.availability.load(path, now=NOW)

    for ahead in (timedelta(seconds=61), timedelta(minutes=5), timedelta(hours=3)):
        future = write(NOW + ahead)
        assert {v["state"] for v in future["providers"].values()} == {"unknown"}
        assert future["providers"]["anthropic"]["stale"] is True
        summary = plugin.availability.format_summary(future, now=NOW)
        assert "EXHAUSTED" not in summary
        assert "in the future" in summary
    for skew in (timedelta(seconds=0), timedelta(seconds=30), timedelta(seconds=60)):
        assert write(NOW + skew)["providers"]["anthropic"]["state"] == "exhausted"  # small clock skew is fine


def test_reader_degrades_extreme_or_wrongly_typed_numbers_to_unknown(tmp_path, monkeypatch):
    """U6: a parseable but numerically absurd entry is unknown for that provider and never an exception."""
    plugin = load_plugin_package(monkeypatch)
    registered = {}

    class Ctx:
        def register_tool(self, **kwargs):
            registered["tool"] = kwargs

        def register_command(self, name, handler, **kwargs):
            registered["command"] = handler

    plugin.register(Ctx())
    path = tmp_path / "availability.json"
    monkeypatch.setattr(plugin.availability, "availability_path", lambda: path)
    valid = '{"state": "low", "remaining_percent": 9, "window": "week", "reset_at": null, "stale": false}'
    for bad in ("1e999", "-1e999", "NaN", "Infinity", "true", "false", "-0.5", "100.5", "250", '"50"', "[50]", "{}"):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        path.write_text(
            '{"schema": 1, "generated_at": "%s", "providers": {"anthropic": {"state": "ok", '
            '"remaining_percent": %s, "window": "session", "reset_at": null, "stale": false},'
            '"zai": %s}}' % (now, bad, valid),
            encoding="utf-8",
        )
        document = plugin.availability.load(path)
        assert document["providers"]["anthropic"]["state"] == "unknown", bad
        assert document["providers"]["anthropic"]["remaining_percent"] is None
        assert document["providers"]["zai"] == {"state": "low", "remaining_percent": 9, "window": "week",
                                                "reset_at": None, "stale": False}
        output = json.loads(registered["tool"]["handler"]({}))
        assert "error" not in output, bad
        assert "anthropic: unknown" in output["summary"] and "zai: low - 9% left" in output["summary"]
        assert "zai: low" in registered["command"]("")
    for edge, expected in (("0", 0), ("100", 100), ("0.0", 0), ("99.9", 99), ("null", None)):
        path.write_text(json.dumps({"schema": 1, "generated_at": now, "providers": {
            "anthropic": {"state": "ok", "remaining_percent": json.loads(edge), "window": "session",
                          "reset_at": None, "stale": False}}}), encoding="utf-8")
        entry = plugin.availability.load(path)["providers"]["anthropic"]
        assert (entry["state"], entry["remaining_percent"]) == ("ok", expected)


def test_reader_keeps_only_contract_fields(tmp_path, monkeypatch):
    plugin = load_plugin_package(monkeypatch)
    path = tmp_path / "availability.json"
    path.write_text(json.dumps({
        "schema": 1,
        "generated_at": "2026-11-06T12:00:00Z",
        "providers": {
            "anthropic": {"state": "ok", "remaining_percent": 50, "window": "week", "reset_at": None,
                          "stale": False, "email": "redacted-address", "token": "x"},
            "zai": {"state": "bogus"},
        },
    }), encoding="utf-8")
    document = plugin.availability.load(path, now=NOW)
    assert set(document["providers"]["anthropic"]) == {"state", "remaining_percent", "window", "reset_at", "stale"}
    assert document["providers"]["zai"]["state"] == "unknown"


def test_agent_tool_and_command_are_registered_and_never_raise(tmp_path, monkeypatch):
    plugin = load_plugin_package(monkeypatch)
    registered = {}

    class Ctx:
        def register_tool(self, **kwargs):
            registered["tool"] = kwargs

        def register_command(self, name, handler, **kwargs):
            registered["command"] = (name, handler, kwargs)

    plugin.register(Ctx())
    assert registered["tool"]["name"] == "usage_availability"
    assert registered["tool"]["schema"]["name"] == "usage_availability"
    assert registered["command"][0] == "usage-availability"

    path = tmp_path / "availability.json"
    path.write_text(json.dumps({
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "providers": {"anthropic": {"state": "low", "remaining_percent": 9, "window": "week",
                                    "reset_at": None, "stale": False}},
    }), encoding="utf-8")
    monkeypatch.setattr(plugin.availability, "availability_path", lambda: path)
    output = json.loads(registered["tool"]["handler"]({}))
    assert "anthropic: low - 9% left (week)" in output["summary"]
    assert output["providers"]["anthropic"]["state"] == "low"
    assert "anthropic: low" in registered["command"][1]("")

    def explode():
        raise OSError("disk on fire")

    monkeypatch.setattr(plugin.availability, "availability_path", explode)
    missing = json.loads(registered["tool"]["handler"]({}))
    assert "No quota data yet" in missing["summary"]


def test_plugin_yaml_declares_the_tool_and_a_real_floor():
    yaml = pytest.importorskip("yaml")
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["provides_tools"] == ["usage_availability"]
    assert manifest["requires_hermes"].startswith(">=0.22")
    assert manifest["license"] == "MIT"
    assert manifest["homepage"].startswith("https://")
    assert manifest["version"] == "1.4.1"
    assert json.loads((ROOT / "dashboard" / "manifest.json").read_text(encoding="utf-8"))["version"] == "1.4.1"
    assert 'VERSION = "1.4.1"' in (ROOT / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")
    assert {"low_threshold_percent", "alerts_enabled", "alert_warn_percent", "alert_critical_percent",
            "kimi_desktop_log_fallback"} <= set(manifest["config_schema"])


# --- end to end: every collector, fake credentials, nothing secret in any output ----------------

SECRETS = {
    "anthropic": "cc-ANTHROPIC-SECRET-VALUE",
    "kimi": "KIMI-SECRET-VALUE",
    "zai": "ZAI-SECRET-VALUE",
    "codex": "CODEX-SECRET-VALUE",
}


def wire_all_providers(monkeypatch, backend, calls):
    codex_window = SimpleNamespace(
        label="Session", used_percent=100.0, reset_at=NOW + timedelta(hours=3), detail=None, scope="account"
    )
    codex = SimpleNamespace(
        unavailable_reason=None, windows=(codex_window,), available=True, plan="Plus", source="usage_api",
        fetched_at=datetime.now(timezone.utc), details=(),
    )
    nous_window = SimpleNamespace(label="Subscription", used_percent=30.0, reset_at=None, detail=None, scope="account")
    nous = SimpleNamespace(
        unavailable_reason=None, windows=(nous_window,), available=True, plan="Pro", source="nous",
        fetched_at=datetime.now(timezone.utc), details=("Renews: 2026-12-01T00:00:00Z",),
    )
    install_account_usage(
        monkeypatch,
        resolve_anthropic_token=lambda: SECRETS["anthropic"],
        fetch_account_usage=lambda provider, **kwargs: codex,
        build_nous_credits_snapshot=lambda account: nous,
    )
    install_secrets(monkeypatch, {"KIMI_API_KEY": SECRETS["kimi"], "GLM_API_KEY": SECRETS["zai"]})
    nous_account = types.ModuleType("hermes_cli.nous_account")
    nous_account.get_nous_portal_account_info = lambda force_fresh: object()
    monkeypatch.setitem(sys.modules, "hermes_cli", types.ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.nous_account", nous_account)
    fake_http(
        monkeypatch,
        backend,
        {
            ANTHROPIC_URL: {
                "five_hour": {"utilization": 23.0, "resets_at": iso(timedelta(hours=4))},
                "seven_day": {"utilization": 10.0, "resets_at": iso(timedelta(days=3))},
                "seven_day_opus": {"utilization": 99.0, "resets_at": iso(timedelta(days=3))},
            },
            "/coding/v1/usages": {"usage": {"limit": 100, "used": 91}},
            "/quota/limit": {"data": {"limits": [
                {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 40}]}},
            "/subscription/list": {"data": []},
        },
        calls,
    )


def test_collect_publishes_availability_and_leaks_no_credential(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    calls = []
    wire_all_providers(monkeypatch, backend, calls)
    payload = backend._collect()

    # Every credential really was used for its own provider (so the test below is meaningful) ...
    assert {c["headers"].get("Authorization") for c in calls} == {
        f"Bearer {SECRETS['anthropic']}", f"Bearer {SECRETS['kimi']}", f"Bearer {SECRETS['zai']}"}
    # ... and none of it, nor any auth wording, appears in a card or in the published file.
    file_text = (tmp_path / "plugin-data" / "availability.json").read_text(encoding="utf-8")
    cache_text = (tmp_path / "plugin-data" / "anthropic-cache.json").read_text(encoding="utf-8")
    for text in (json.dumps(payload), file_text, cache_text):
        lowered = text.lower()
        for secret in SECRETS.values():
            assert secret not in text
        for forbidden in ("authorization", "bearer", "access_token", "refresh_token", "api_key"):
            assert forbidden not in lowered

    document = json.loads(file_text)
    providers = document["providers"]
    assert providers["anthropic"]["state"] == "ok" and providers["anthropic"]["remaining_percent"] == 77
    assert providers["openai-codex"]["state"] == "exhausted"
    assert providers["kimi-coding"]["state"] == "low" and providers["kimi-coding"]["remaining_percent"] == 9
    assert providers["zai"]["state"] == "ok"
    assert providers["nous"]["state"] == "ok" and providers["nous"]["window"] == "month"
    assert payload["settings"] == {"alerts_enabled": True, "alert_warn_percent": 80, "alert_critical_percent": 95}


def test_every_published_state_is_unknown_when_nothing_could_be_read(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    for name in ("_codex_card", "_anthropic_card", "_kimi_card", "_zai_card", "_nous_card"):
        monkeypatch.setattr(backend, name, lambda: backend._unavailable("x", "X", "offline"))
    backend._collect()
    document = json.loads((tmp_path / "plugin-data" / "availability.json").read_text(encoding="utf-8"))
    assert {v["state"] for v in document["providers"].values()} == {"unknown"}


def test_availability_write_failure_never_breaks_collection(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)

    def broken():
        raise OSError("read-only disk")

    monkeypatch.setattr(backend, "_data_dir", broken)
    for name in ("_codex_card", "_anthropic_card", "_kimi_card", "_zai_card", "_nous_card"):
        monkeypatch.setattr(backend, name, lambda: backend._unavailable("x", "X", "offline"))
    assert backend._collect()["summary"]["total"] == 5


def test_sample_snapshot_helper_still_describes_an_account_window():
    assert sample_snapshot().windows[0].scope == "account"
