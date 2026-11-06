"""Offline regression tests: collectors never touch credentials or the network, and the plugin
never installs or replaces a Hermes secret scope (Hermes >= 0.22 binds one on every plugin route)."""
import json
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Barrier

import pytest
from test_backend import install_account_usage, load_backend, sample_snapshot


@pytest.fixture
def core(monkeypatch, tmp_path):
    scope = ContextVar("test_secret_scope", default=None)
    home = ContextVar("test_home", default=None)
    launch_home = tmp_path / "launch"
    calls = []
    secrets = types.ModuleType("agent.secret_scope")

    class UnscopedSecretError(RuntimeError):
        pass

    secrets.UnscopedSecretError = UnscopedSecretError
    secrets.current_secret_scope = scope.get

    def refuse_to_bind(*_args, **_kwargs):
        calls.append("bound")  # the plugin must never bind or replace a scope
        raise AssertionError("plugin tried to install a secret scope")

    secrets.set_secret_scope = refuse_to_bind
    secrets.get_secret = lambda name, default=None: default
    constants = types.ModuleType("hermes_constants")
    constants.get_process_hermes_home = lambda: launch_home
    constants.get_hermes_home = lambda: home.get() or launch_home
    agent = types.ModuleType("agent")
    agent.secret_scope = secrets
    for name, module in (("agent", agent), ("agent.secret_scope", secrets), ("hermes_constants", constants)):
        monkeypatch.setitem(sys.modules, name, module)
    return types.SimpleNamespace(scope=scope, home=home, launch_home=launch_home, calls=calls,
                                 error=UnscopedSecretError)


def fake_collectors(backend, monkeypatch, callback):
    for name in ("_codex_card", "_anthropic_card", "_kimi_card", "_zai_card", "_nous_card"):
        monkeypatch.setattr(backend, name, callback)


def offline(backend, monkeypatch, *names):
    for name in names:
        monkeypatch.setattr(backend, name, lambda: backend._unavailable("test", "Test", "offline"))


def test_anthropic_reports_sanitized_missing_scope_category(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)

    def resolve():
        raise core.error("private exception text must not leave backend")

    install_account_usage(monkeypatch, resolve_anthropic_token=resolve)
    card = backend._public_card(backend._anthropic_card())
    assert card["failure_category"] == "missing_profile_scope"
    assert card["available"] is False
    assert "profile scope" in card["reason"]
    assert "private exception text" not in json.dumps(card)


def test_request_without_scope_reports_missing_profile_scope_and_never_rebinds(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    monkeypatch.setattr(backend, "_collect", lambda: pytest.fail("must not collect without a scope"))
    result = backend.get_usage()
    assert [card["failure_category"] for card in result["providers"]] == ["missing_profile_scope"] * 5
    assert all(card["available"] is False for card in result["providers"])
    assert result["summary"]["available"] == 0
    assert core.scope.get() is None
    assert core.calls == []
    assert not (tmp_path / "plugin-data" / "availability.json").exists()  # nothing is published either


def test_home_without_scope_is_also_reported_not_collected(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    monkeypatch.setattr(backend, "_collect", lambda: pytest.fail("must not collect"))
    token = core.home.set(tmp_path / "other")
    try:
        result = backend.get_usage()
        assert result["providers"][0]["failure_category"] == "missing_profile_scope"
        assert core.scope.get() is None
    finally:
        core.home.reset(token)
    assert core.calls == []


def test_core_bound_launch_scope_keeps_launch_caches(tmp_path, monkeypatch, core):
    # Hermes >= 0.22 binds the launch profile's scope on every plugin route itself.
    backend = load_backend(tmp_path, monkeypatch)
    calls = []

    def fetch_card():
        calls.append(core.scope.get()["marker"])
        return backend._snapshot_to_card("anthropic", "Claude Code", sample_snapshot())

    monkeypatch.setattr(backend, "_fetch_anthropic_usage_card", fetch_card)
    offline(backend, monkeypatch, "_codex_card", "_kimi_card", "_zai_card", "_nous_card")
    scope = {"marker": "core-launch"}
    st = core.scope.set(scope)
    ht = core.home.set(core.launch_home)
    try:
        first = backend.get_usage()
        second = backend.get_usage()
        assert second is first  # process response cache deduplicates polling
        assert calls == ["core-launch"]
        assert core.scope.get() is scope
        assert core.home.get() == core.launch_home
    finally:
        core.home.reset(ht)
        core.scope.reset(st)
    assert core.calls == []  # never binds over the core's scope
    assert (tmp_path / "plugin-data" / "anthropic-cache.json").is_file()
    assert (tmp_path / "plugin-data" / "availability.json").is_file()


def test_routed_scopes_never_share_response_or_anthropic_cache(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    backend._write_anthropic_cache(backend._snapshot_to_card("anthropic", "Claude Code", sample_snapshot()))
    cache_path = backend._anthropic_cache_path()
    cache_before = cache_path.read_bytes()
    launch_payload = {"launch_only": True}
    backend._CACHE.update(at=backend.time.monotonic(), payload=launch_payload)

    def anthropic():
        snapshot = sample_snapshot()
        snapshot.plan = core.scope.get()["marker"]
        assert backend._read_anthropic_cache(backend._ANTHROPIC_STALE_SECONDS) is None  # launch cache is invisible
        return backend._snapshot_to_card("anthropic", "Claude Code", snapshot)

    monkeypatch.setattr(backend, "_anthropic_card", anthropic)
    offline(backend, monkeypatch, "_codex_card", "_kimi_card", "_zai_card", "_nous_card")
    for marker in ("alpha", "beta"):
        scope = {"marker": marker}
        st = core.scope.set(scope)
        ht = core.home.set(tmp_path / marker)
        try:
            result = backend.get_usage()
            assert result["providers"][1]["plan"] == marker
            assert core.scope.get() is scope
            assert core.home.get() == tmp_path / marker
        finally:
            core.home.reset(ht)
            core.scope.reset(st)
    assert core.calls == []
    assert cache_path.read_bytes() == cache_before
    assert backend._CACHE["payload"] is launch_payload


def test_concurrent_requests_keep_scope_and_home_isolated(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    barrier = Barrier(10)

    def collect():
        marker = core.scope.get()["marker"]
        home = core.home.get().name
        barrier.wait(timeout=5)
        return backend._unavailable("test", "Test", f"{marker}:{home}")

    fake_collectors(backend, monkeypatch, collect)

    def request(marker):
        scope = {"marker": marker}
        st = core.scope.set(scope)
        ht = core.home.set(tmp_path / marker)
        try:
            result = backend.get_usage()
            assert core.scope.get() is scope
            assert core.home.get() == tmp_path / marker
            assert backend._USE_PROCESS_CACHE.get() is True
            return [card["reason"] for card in result["providers"]]
        finally:
            core.home.reset(ht)
            core.scope.reset(st)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(request, ("alpha", "beta")))
    assert results == [["alpha:alpha"] * 5, ["beta:beta"] * 5]
    assert core.scope.get() is None
    assert core.calls == []


@pytest.mark.parametrize("routed", [False, True])
def test_scope_and_cache_flag_restored_when_collection_raises(tmp_path, monkeypatch, core, routed):
    backend = load_backend(tmp_path, monkeypatch)

    def explode():
        raise RuntimeError("collection failed")

    monkeypatch.setattr(backend, "_collect", explode)
    scope = {}  # an empty scope is still a scope: never replace it
    token = core.scope.set(scope)
    home_token = core.home.set(tmp_path / "routed" if routed else core.launch_home)
    try:
        with pytest.raises(RuntimeError, match="collection failed"):
            backend.get_usage()
        assert core.scope.get() is scope
        assert backend._USE_PROCESS_CACHE.get() is True
    finally:
        core.home.reset(home_token)
        core.scope.reset(token)
    assert core.calls == []


def test_core_without_scope_module_fails_closed(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", None)
    monkeypatch.setattr(backend, "_collect", lambda: pytest.fail("must not collect"))
    result = backend.get_usage()
    assert result["providers"][0]["failure_category"] == "missing_profile_scope"


def test_incomplete_scope_api_fails_closed(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    del sys.modules["agent.secret_scope"].current_secret_scope
    monkeypatch.setattr(backend, "_collect", lambda: pytest.fail("must not collect"))
    assert backend.get_usage()["providers"][0]["failure_category"] == "missing_profile_scope"


def test_launch_response_cache_still_deduplicates(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    calls = []

    def collect():
        calls.append(core.scope.get()["marker"])
        return {"cached": True}

    monkeypatch.setattr(backend, "_collect", collect)
    token = core.scope.set({"marker": "launch"})
    try:
        assert backend.get_usage() == backend.get_usage() == {"cached": True}
    finally:
        core.scope.reset(token)
    assert calls == ["launch"]


def test_installed_core_scope_contract(tmp_path, monkeypatch):
    # Optional integration: put the installed Hermes source on PYTHONPATH.
    secrets = pytest.importorskip("agent.secret_scope")
    constants = pytest.importorskip("hermes_constants")
    backend = load_backend(tmp_path, monkeypatch)
    home = tmp_path / "launch-integration"
    monkeypatch.setattr(constants, "get_process_hermes_home", lambda: home)
    old_mode = secrets.is_multiplex_active()
    ht = constants.set_hermes_home_override(home)
    st = secrets.set_secret_scope(None)
    secrets.set_multiplex_active(True)

    def collect():
        return backend._unavailable("test", "Test", secrets.get_secret("USAGE_DASHBOARD_TEST") or "none")

    fake_collectors(backend, monkeypatch, collect)
    try:
        # No scope: the plugin reports it and does NOT bind the launch scope itself.
        result = backend.get_usage()
        assert [card["failure_category"] for card in result["providers"]] == ["missing_profile_scope"] * 5
        assert secrets.current_secret_scope() is None
        # A routed profile (foreign home + its own scope) is collected without launch caches.
        routed_home = constants.set_hermes_home_override(tmp_path / "routed-profile")
        request_token = secrets.set_secret_scope({"USAGE_DASHBOARD_TEST": "request"})
        try:
            result = backend.get_usage()
            assert [card["reason"] for card in result["providers"]] == ["request"] * 5
        finally:
            secrets.reset_secret_scope(request_token)
            constants.reset_hermes_home_override(routed_home)
        # The launch profile under a core-bound scope (Hermes >= 0.22) uses its own caches.
        launch_token = secrets.set_secret_scope({"USAGE_DASHBOARD_TEST": "launch"})
        try:
            result = backend.get_usage()
            assert [card["reason"] for card in result["providers"]] == ["launch"] * 5
        finally:
            secrets.reset_secret_scope(launch_token)
    finally:
        secrets.set_multiplex_active(old_mode)
        secrets.reset_secret_scope(st)
        constants.reset_hermes_home_override(ht)


def test_collect_copies_a_separate_context_for_every_worker(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    marker = ContextVar("request_marker", default="lost")
    barrier = Barrier(5)

    def collect():
        inherited = marker.get()
        marker.set("worker-only")
        barrier.wait(timeout=5)
        return backend._unavailable("test", "Test", inherited)

    fake_collectors(backend, monkeypatch, collect)
    token = marker.set("request")
    try:
        result = backend._collect()
        assert [card["reason"] for card in result["providers"]] == ["request"] * 5
        assert marker.get() == "request"
    finally:
        marker.reset(token)
