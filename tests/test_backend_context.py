"""Offline regression tests: collectors never touch credentials or the network."""
import json
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Barrier

import pytest
from test_backend import install_fake_account_usage, load_backend, sample_snapshot


def test_anthropic_reports_sanitized_missing_scope_category(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    def fetch():
        raise core.error("private exception text must not leave backend")
    install_fake_account_usage(monkeypatch, fetch)
    card = backend._public_card(backend._anthropic_card())
    assert card["failure_category"] == "missing_profile_scope"
    assert card["available"] is False
    assert "profile scope" in card["reason"]
    assert "private exception text" not in json.dumps(card)


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
    secrets.set_secret_scope = scope.set
    secrets.reset_secret_scope = scope.reset
    constants = types.ModuleType("hermes_constants")
    constants.get_process_hermes_home = lambda: launch_home
    constants.get_hermes_home = lambda: home.get() or launch_home
    policy = types.ModuleType("tui_gateway.launch_profile_policy")
    def launch_secret_scope(path):
        calls.append(path)
        return {"marker": "launch"}
    policy.launch_secret_scope = launch_secret_scope
    for name, module in (("agent.secret_scope", secrets), ("hermes_constants", constants),
                         ("tui_gateway.launch_profile_policy", policy)):
        monkeypatch.setitem(sys.modules, name, module)
    return types.SimpleNamespace(scope=scope, home=home, launch_home=launch_home,
                                 calls=calls, policy=policy, error=UnscopedSecretError)


def fake_collectors(backend, monkeypatch, callback):
    for name in ("_codex_card", "_anthropic_card", "_kimi_card", "_zai_card", "_nous_card"):
        monkeypatch.setattr(backend, name, callback)


def test_route_establishes_launch_scope_and_restores_it(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    def collect():
        current = core.scope.get()
        return backend._unavailable("test", "Test", current["marker"] if current else "lost")
    fake_collectors(backend, monkeypatch, collect)
    result = backend.get_usage()
    assert [card["reason"] for card in result["providers"]] == ["launch"] * 5
    assert core.calls == [core.launch_home]
    assert core.scope.get() is None
    assert core.home.get() is None


def test_core_bound_launch_scope_keeps_launch_caches_and_plan(tmp_path, monkeypatch, core):
    # Hermes >= 0.22 binds the launch profile's scope on every plugin route itself.
    backend = load_backend(tmp_path, monkeypatch)
    monkeypatch.setattr(backend, "_anthropic_plan_from_claude_config", lambda: "Max")
    calls = []
    def fetch():
        calls.append(core.scope.get()["marker"])
        return sample_snapshot()
    install_fake_account_usage(monkeypatch, fetch)
    for name in ("_codex_card", "_kimi_card", "_zai_card", "_nous_card"):
        monkeypatch.setattr(backend, name, lambda: backend._unavailable("test", "Test", "offline"))
    scope = {"marker": "core-launch"}
    st = core.scope.set(scope)
    ht = core.home.set(core.launch_home)
    try:
        first = backend.get_usage()
        second = backend.get_usage()
        assert second is first  # process response cache deduplicates polling
        assert first["providers"][1]["plan"] == "Max"
        assert calls == ["core-launch"]
        assert core.scope.get() is scope
        assert core.home.get() == core.launch_home
    finally:
        core.home.reset(ht)
        core.scope.reset(st)
    assert core.calls == []  # never rebinds over the core's scope
    expected = core.launch_home / "cache" / "usage-dashboard" / "anthropic.json"
    assert expected.is_file()


@pytest.mark.parametrize("routed_home", [True])
def test_existing_scopes_never_share_response_or_anthropic_cache(tmp_path, monkeypatch, core, routed_home):
    backend = load_backend(tmp_path, monkeypatch)
    monkeypatch.setattr(backend, "_anthropic_plan_from_claude_config", lambda: "Launch-only plan")
    backend._write_anthropic_cache(backend._snapshot_to_card("anthropic", "Claude Code", sample_snapshot()))
    cache_before = backend._ANTHROPIC_CACHE_FILE.read_bytes()
    launch_payload = {"launch_only": True}
    backend._CACHE.update(at=backend.time.monotonic(), payload=launch_payload)
    def fetch():
        snapshot = sample_snapshot()
        snapshot.plan = core.scope.get()["marker"]
        return snapshot
    install_fake_account_usage(monkeypatch, fetch)
    for name in ("_codex_card", "_kimi_card", "_zai_card", "_nous_card"):
        monkeypatch.setattr(backend, name, lambda: backend._unavailable("test", "Test", "offline"))
    for marker in ("alpha", "beta"):
        scope = {"marker": marker}
        st = core.scope.set(scope)
        ht = core.home.set(tmp_path / marker if routed_home else core.launch_home)
        try:
            result = backend.get_usage()
            assert result["providers"][1]["plan"] == marker
            assert core.scope.get() is scope
            assert core.home.get() == (tmp_path / marker if routed_home else core.launch_home)
        finally:
            core.home.reset(ht)
            core.scope.reset(st)
    assert core.calls == []
    assert backend._ANTHROPIC_CACHE_FILE.read_bytes() == cache_before
    assert backend._CACHE["payload"] is launch_payload


def test_launch_cache_is_under_launch_home_not_shared_root(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    monkeypatch.setattr(backend, "_anthropic_plan_from_claude_config", lambda: None)
    install_fake_account_usage(monkeypatch, sample_snapshot)
    for name in ("_codex_card", "_kimi_card", "_zai_card", "_nous_card"):
        monkeypatch.setattr(backend, name, lambda: backend._unavailable("test", "Test", "offline"))
    backend.get_usage()
    expected = core.launch_home / "cache" / "usage-dashboard" / "anthropic.json"
    assert expected.is_file()
    assert not backend._ANTHROPIC_CACHE_FILE.exists()



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


@pytest.mark.parametrize("existing", [False, True])
def test_scope_restored_when_collection_raises(tmp_path, monkeypatch, core, existing):
    backend = load_backend(tmp_path, monkeypatch)
    def explode():
        raise RuntimeError("collection failed")
    monkeypatch.setattr(backend, "_collect", explode)
    scope = {} if existing else None  # Empty scope is present, never replace it.
    token = core.scope.set(scope)
    try:
        with pytest.raises(RuntimeError, match="collection failed"):
            backend.get_usage()
        assert core.scope.get() is scope
        assert backend._USE_PROCESS_CACHE.get() is True
        assert backend._ANTHROPIC_CACHE_PATH.get() is None
        assert core.calls == ([] if existing else [core.launch_home])
    finally:
        core.scope.reset(token)


def test_home_without_scope_is_rejected_before_collection(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    monkeypatch.setattr(backend, "_collect", lambda: pytest.fail("must not collect"))
    token = core.home.set(tmp_path / "other")
    try:
        with pytest.raises(core.error):
            backend.get_usage()
        assert core.home.get() == tmp_path / "other"
        assert core.scope.get() is None
        assert core.calls == []
    finally:
        core.home.reset(token)


def test_old_core_without_scope_module_keeps_legacy_behavior(tmp_path, monkeypatch):
    backend = load_backend(tmp_path, monkeypatch)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", None)
    monkeypatch.setattr(backend, "_collect", lambda: {"legacy": True})
    assert backend.get_usage() == {"legacy": True}


def test_incomplete_scope_api_does_not_silently_fall_back(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    del sys.modules["agent.secret_scope"].current_secret_scope
    monkeypatch.setattr(backend, "_collect", lambda: pytest.fail("must not collect"))
    with pytest.raises((ImportError, AttributeError)):
        backend.get_usage()


def test_missing_launch_api_fails_closed(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    del core.policy.launch_secret_scope
    monkeypatch.setattr(backend, "_collect", lambda: pytest.fail("must not collect"))
    with pytest.raises(ImportError):
        backend.get_usage()
    assert core.scope.get() is None


def test_launch_response_cache_still_deduplicates(tmp_path, monkeypatch, core):
    backend = load_backend(tmp_path, monkeypatch)
    calls = []
    def collect():
        calls.append(core.scope.get()["marker"])
        return {"cached": True}
    monkeypatch.setattr(backend, "_collect", collect)
    assert backend.get_usage() == backend.get_usage() == {"cached": True}
    assert calls == ["launch"]
    assert core.scope.get() is None


def test_installed_core_scope_and_fail_closed_contract(tmp_path, monkeypatch):
    # Optional integration: put the installed Hermes source on PYTHONPATH.
    secrets = pytest.importorskip("agent.secret_scope")
    constants = pytest.importorskip("hermes_constants")
    policy = pytest.importorskip("tui_gateway.launch_profile_policy")
    backend = load_backend(tmp_path, monkeypatch)
    home = tmp_path / "launch-integration"
    monkeypatch.setattr(constants, "get_process_hermes_home", lambda: home)
    # Use the REAL launch builder + scope API, but only synthetic source maps.
    monkeypatch.setattr(policy, "_launch_env", lambda: {"USAGE_DASHBOARD_TEST": "launch"})
    monkeypatch.setattr(secrets, "build_profile_secret_scope", lambda path: {})
    old_mode = secrets.is_multiplex_active()
    ht = constants.set_hermes_home_override(home)
    st = secrets.set_secret_scope(None)
    secrets.set_multiplex_active(True)
    def collect():
        marker = secrets.get_secret("USAGE_DASHBOARD_TEST")
        return backend._unavailable("test", "Test", marker)
    fake_collectors(backend, monkeypatch, collect)
    try:
        with pytest.raises(secrets.UnscopedSecretError):
            secrets.get_secret("USAGE_DASHBOARD_TEST")
        result = backend.get_usage()
        assert [card["reason"] for card in result["providers"]] == ["launch"] * 5
        assert secrets.current_secret_scope() is None
        with pytest.raises(secrets.UnscopedSecretError):
            secrets.get_secret("USAGE_DASHBOARD_TEST")
        # A routed profile (foreign home + its own scope) must never see the launch response.
        routed_home = constants.set_hermes_home_override(tmp_path / "routed-profile")
        request_token = secrets.set_secret_scope({"USAGE_DASHBOARD_TEST": "request"})
        try:
            result = backend.get_usage()
            assert [card["reason"] for card in result["providers"]] == ["request"] * 5
            assert secrets.get_secret("USAGE_DASHBOARD_TEST") == "request"
        finally:
            secrets.reset_secret_scope(request_token)
            constants.reset_hermes_home_override(routed_home)
        # The launch profile under a core-bound scope (Hermes >= 0.22) reuses its own cache.
        launch_token = secrets.set_secret_scope(policy.launch_secret_scope(home))
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

    for name in ("_codex_card", "_anthropic_card", "_kimi_card", "_zai_card", "_nous_card"):
        monkeypatch.setattr(backend, name, collect)
    token = marker.set("request")
    try:
        result = backend._collect()
        assert [card["reason"] for card in result["providers"]] == ["request"] * 5
        assert marker.get() == "request"
    finally:
        marker.reset(token)
