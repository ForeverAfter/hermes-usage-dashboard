"""Backend for the Hermes Desktop subscription-usage dashboard.

Only sanitized usage data leaves this module. Credentials stay inside Hermes's
secret scope (or, for Codex, inside Hermes's own usage fetcher) and are never
included in responses, caches, ``availability.json`` or logs.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import ContextVar, copy_context
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter

router = APIRouter()
logger = logging.getLogger(__name__)

PLUGIN_NAME = "usage-dashboard"
VERSION = "1.4.1"

_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 120.0
_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
_COLLECT_LOCK = threading.Lock()
_ANTHROPIC_FRESH_SECONDS = 300.0
_ANTHROPIC_STALE_SECONDS = 86_400.0
_USE_PROCESS_CACHE: ContextVar[bool] = ContextVar("usage_process_cache", default=True)

# Provider cards in display order: (card id, label, Hermes provider id used in availability.json).
_PROVIDERS = (
    ("openai-codex", "OpenAI Codex", "openai-codex"),
    ("anthropic", "Claude Code", "anthropic"),
    ("kimi", "Kimi / Moonshot", "kimi-coding"),
    ("zai", "ZCode / Z.ai", "zai"),
    ("nous", "Nous Research", "nous"),
)

# Keys under plugins.entries.usage-dashboard.settings (declared in plugin.yaml `config_schema`).
_DEFAULT_SETTINGS: dict[str, Any] = {
    "low_threshold_percent": 15,
    "alerts_enabled": True,
    "alert_warn_percent": 80,
    "alert_critical_percent": 95,
    "kimi_desktop_log_fallback": False,
}

# Secrets are read with agent.secret_scope.get_secret (declared as optional_env in plugin.yaml).
_KIMI_KEY_ENV = ("KIMI_CODING_API_KEY", "KIMI_API_KEY")  # the Kimi Code key first
_KIMI_CODE_KEY_PREFIX = "sk-kimi-"  # Kimi Code keys; other KIMI_API_KEY values are Moonshot platform keys
_ZAI_KEY_ENV = ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _data_dir() -> Path:
    """Per-plugin data root (survives update/remove and follows the active profile)."""
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME)


def _plugin_settings() -> dict[str, Any]:
    """Validated plugin settings; a missing or ill-typed key falls back to its default."""
    settings = dict(_DEFAULT_SETTINGS)
    try:
        from hermes_cli.config import load_config_readonly

        entries = ((load_config_readonly() or {}).get("plugins") or {}).get("entries") or {}
        configured = (entries.get(PLUGIN_NAME) or {}).get("settings") or {}
    except Exception:
        configured = {}
    if not isinstance(configured, dict):
        return settings
    for key, default in _DEFAULT_SETTINGS.items():
        value = configured.get(key, default)
        if isinstance(default, bool):
            settings[key] = value if isinstance(value, bool) else default
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and 1 <= value <= 100:
            settings[key] = int(value)
    if settings["alert_critical_percent"] <= settings["alert_warn_percent"]:
        settings["alert_warn_percent"] = _DEFAULT_SETTINGS["alert_warn_percent"]
        settings["alert_critical_percent"] = _DEFAULT_SETTINGS["alert_critical_percent"]
    return settings


def _window_order(window: dict[str, Any]) -> int:
    """0 = session / 5 hours, 1 = week, 2 = month or subscription period, 3 = anything else."""
    label = str(window.get("label") or "").casefold()
    if "5-hour" in label or "five hour" in label or label in {"session", "current session"}:
        return 0
    if "week" in label:
        return 1
    if "month" in label or "subscription period" in label:
        return 2
    return 3


def _unavailable(provider: str, label: str, reason: str, source: str = "local") -> dict[str, Any]:
    return {
        "provider": provider,
        "label": label,
        "available": False,
        "plan": None,
        "source": source,
        "fetched_at": _iso(_now()),
        "windows": [],
        "details": [],
        "reason": reason[:240],
    }


def _missing_scope_card(provider: str, label: str, source: str = "local") -> dict[str, Any]:
    """Internal scope failure: fixed text, never the exception, never a stale card."""
    card = _unavailable(provider, label, f"{label} usage requires an active Hermes profile scope.", source)
    card["failure_category"] = "missing_profile_scope"
    return card


def _public_window(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": str(window.get("label") or ""),
        "used_percent": window.get("used_percent") if isinstance(window.get("used_percent"), (int, float)) else None,
        "remaining_percent": (
            window.get("remaining_percent")
            if isinstance(window.get("remaining_percent"), (int, float))
            else None
        ),
        "reset_at": window.get("reset_at") if isinstance(window.get("reset_at"), str) else None,
        "detail": window.get("detail") if isinstance(window.get("detail"), str) else None,
        # "model": caps one model family (Opus / Sonnet weekly); it never means the account is out.
        "scope": "model" if window.get("scope") == "model" else "account",
    }


def _public_card(card: dict[str, Any]) -> dict[str, Any]:
    windows = card.get("windows") if isinstance(card.get("windows"), list) else []
    details = card.get("details") if isinstance(card.get("details"), list) else []
    return {
        "provider": str(card.get("provider") or ""),
        "label": str(card.get("label") or card.get("provider") or ""),
        "available": bool(card.get("available")),
        "plan": card.get("plan") if isinstance(card.get("plan"), str) else None,
        "source": str(card.get("source") or "local"),
        "fetched_at": card.get("fetched_at") if isinstance(card.get("fetched_at"), str) else None,
        "renewal_at": card.get("renewal_at") if isinstance(card.get("renewal_at"), str) else None,
        "stale": bool(card.get("stale")),
        "local": bool(card.get("local")),
        "windows": [_public_window(window) for window in windows if isinstance(window, dict)],
        "details": [detail for detail in details if isinstance(detail, str)],
        "reason": card.get("reason") if isinstance(card.get("reason"), str) else None,
        "failure_category": (
            "missing_profile_scope" if card.get("failure_category") == "missing_profile_scope" else None
        ),
    }


def _snapshot_to_card(provider: str, label: str, snapshot: Any) -> dict[str, Any]:
    if snapshot is None:
        return _unavailable(
            provider,
            label,
            "No compatible session was found, or the provider did not return usage limits.",
            "hermes-account-usage",
        )
    if snapshot.unavailable_reason:
        return _unavailable(provider, label, snapshot.unavailable_reason, snapshot.source)
    windows = []
    for window in snapshot.windows:
        used = None if window.used_percent is None else max(0.0, min(100.0, float(window.used_percent)))
        windows.append(
            {
                "label": window.label,
                "used_percent": used,
                "remaining_percent": None if used is None else max(0.0, 100.0 - used),
                "reset_at": _iso(window.reset_at),
                "detail": window.detail,
                "scope": getattr(window, "scope", "account"),
            }
        )
    return {
        "provider": provider,
        "label": label,
        "available": snapshot.available,
        "plan": snapshot.plan,
        "source": snapshot.source,
        "fetched_at": _iso(snapshot.fetched_at),
        "windows": windows,
        "details": list(snapshot.details),
        "reason": None if snapshot.available else "The provider did not expose any usage windows.",
    }


def _window_has_rolled_over(card: dict[str, Any], now: Optional[datetime] = None) -> bool:
    """True when any cached window's reset time has passed: its percentages describe a
    window that no longer exists, so the cache must not hide the new one."""
    now = now or _now()
    for window in card.get("windows") or []:
        moment = _parse_dt(window.get("reset_at") if isinstance(window, dict) else None)
        if moment is not None and moment <= now:
            return True
    return False


def _payload_has_rolled_over(payload: Any) -> bool:
    providers = payload.get("providers") if isinstance(payload, dict) else None
    return any(isinstance(card, dict) and _window_has_rolled_over(card) for card in providers or [])


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch_ms_iso(value: Any) -> Optional[str]:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        seconds = float(value) / 1000.0 if value > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _normalized_reset(value: Any) -> Optional[str]:
    if isinstance(value, (int, float)):
        return _epoch_ms_iso(value)
    return _iso(_parse_dt(value))


def _is_unscoped_error(exc: BaseException) -> bool:
    try:
        from agent.secret_scope import UnscopedSecretError
    except ImportError:
        return False
    return isinstance(exc, UnscopedSecretError)


def _first_secret(names: tuple[str, ...]) -> str:
    """First non-blank value among ``names`` from the active profile's secret scope."""
    secrets = _declared_secrets(names)
    return secrets[0] if secrets else ""


def _declared_secrets(names: tuple[str, ...]) -> list[str]:
    """Distinct non-blank values of ``names`` from the active profile's secret scope, in order."""
    from agent.secret_scope import get_secret

    values: list[str] = []
    for name in names:
        value = str(get_secret(name) or "").strip()
        if value and value not in values:
            values.append(value)
    return values


# --- Anthropic (Claude Code) ------------------------------------------------------------------
# Hermes core still reads this endpoint's ``utilization`` with ``fraction=True``: any value <= 1 is
# multiplied by 100, so a freshly reset session at 1 % renders as 100 % (upstream #58226). The public
# ``fetch_account_usage("anthropic")`` therefore cannot be used yet, because the original value is
# lost. Remove this block once core reads the value as the percent it is.

_ANTHROPIC_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_ANTHROPIC_WINDOWS = (
    ("five_hour", "Current session", "account"),
    ("seven_day", "Current week", "account"),
    ("seven_day_opus", "Opus week", "model"),
    ("seven_day_sonnet", "Sonnet week", "model"),
)
_ANTHROPIC_API_KEY_PREFIXES = ("sk-ant-api", "sk-ant-usr", "sk-ant-svc", "sk-ant-admin")


def _is_oauth_token(token: str) -> bool:
    """Console API keys cannot read OAuth usage; every other Anthropic token shape can."""
    return not token.startswith(_ANTHROPIC_API_KEY_PREFIXES) and token.startswith(("sk-ant-", "eyJ", "cc-"))


def _anthropic_cache_path() -> Path:
    return _data_dir() / "anthropic-cache.json"


def _read_anthropic_cache(max_age: float) -> Optional[dict[str, Any]]:
    if not _USE_PROCESS_CACHE.get():
        return None
    try:
        entry = json.loads(_anthropic_cache_path().read_text(encoding="utf-8"))
        saved_at = float(entry.get("saved_at") or 0.0)
        card = entry.get("card")
        if time.time() - saved_at > max_age or not isinstance(card, dict):
            return None
        if card.get("provider") != "anthropic" or not isinstance(card.get("windows"), list):
            return None
        if max_age <= _ANTHROPIC_FRESH_SECONDS and _window_has_rolled_over(card):
            return None  # a fresh-cache hit must never outlive the window it describes
        return json.loads(json.dumps(card))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_anthropic_cache(card: dict[str, Any]) -> None:
    if not _USE_PROCESS_CACHE.get() or not card.get("available") or not card.get("windows"):
        return
    try:
        path = _anthropic_cache_path()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"saved_at": time.time(), "card": _public_card(card)}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        pass


def _fetch_anthropic_usage_card() -> dict[str, Any]:
    """Claude OAuth usage with ``utilization`` read as the percent Anthropic returns."""
    from agent.account_usage import resolve_anthropic_token

    token = str(resolve_anthropic_token() or "").strip()
    if not token:
        return _snapshot_to_card("anthropic", "Claude Code", None)
    if not _is_oauth_token(token):
        return _unavailable(
            "anthropic",
            "Claude Code",
            "Anthropic account limits are only available for OAuth-backed Claude accounts.",
            "oauth_usage_api",
        )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": f"hermes-usage-dashboard/{VERSION}",
    }
    with httpx.Client(timeout=15.0) as client:
        response = client.get(_ANTHROPIC_USAGE_URL, headers=headers)
        response.raise_for_status()
    payload = response.json() or {}
    windows = []
    for key, label, scope in _ANTHROPIC_WINDOWS:
        raw = payload.get(key) if isinstance(payload.get(key), dict) else {}
        used = _number(raw.get("utilization"))
        if used is None:
            continue
        used = max(0.0, min(100.0, used))
        windows.append(
            {
                "label": label,
                "used_percent": used,
                "remaining_percent": 100.0 - used,
                "reset_at": _iso(_parse_dt(raw.get("resets_at"))),
                "detail": None,
                "scope": scope,
            }
        )
    details = []
    extra = payload.get("extra_usage") if isinstance(payload.get("extra_usage"), dict) else {}
    used_credits, monthly_limit = _number(extra.get("used_credits")), _number(extra.get("monthly_limit"))
    if extra.get("is_enabled") and used_credits is not None and monthly_limit is not None:
        details.append(f"Extra usage: {used_credits:.2f} / {monthly_limit:.2f} {extra.get('currency') or 'USD'}")
    return {
        "provider": "anthropic",
        "label": "Claude Code",
        "available": bool(windows),
        "plan": None,
        "source": "oauth_usage_api",
        "fetched_at": _iso(_now()),
        "windows": windows,
        "details": details,
        "reason": None if windows else "The provider did not expose any usage windows.",
    }


def _anthropic_card() -> dict[str, Any]:
    fresh = _read_anthropic_cache(_ANTHROPIC_FRESH_SECONDS)
    if fresh is not None:
        fresh["source"] = "oauth_usage_api-cache"
        return fresh

    failure_reason = "Claude Code usage could not be queried temporarily."
    try:
        card = _fetch_anthropic_usage_card()
        if card.get("available"):
            _write_anthropic_cache(card)
            return card
        failure_reason = str(card.get("reason") or failure_reason)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        failure_reason = (
            "Anthropic temporarily rate-limited the usage request (HTTP 429)."
            if status == 429
            else f"Anthropic temporarily rejected the usage request (HTTP {status})."
        )
    except Exception as exc:
        if _is_unscoped_error(exc):
            return _missing_scope_card("anthropic", "Claude Code", "oauth_usage_api")

    stale = _read_anthropic_cache(_ANTHROPIC_STALE_SECONDS)
    if stale is not None:
        stale["source"] = "oauth_usage_api-cache"
        stale["stale"] = True
        stale["details"] = list(stale.get("details") or []) + [failure_reason]
        return stale
    return _unavailable("anthropic", "Claude Code", failure_reason, "oauth_usage_api")


# --- Nous Research ----------------------------------------------------------------------------


def _renewal_from_details(details: list[str]) -> Optional[str]:
    for raw in details:
        text = str(raw)
        for prefix in ("Renews:", "Next renewal:"):
            if text.startswith(prefix):
                return _normalized_reset(text[len(prefix):].strip())
    return None


def _nous_card() -> dict[str, Any]:
    """Fetch the signed-in Nous Portal account and expose only credit totals.

    Hermes has no public usage fetcher for Nous (``usage.bars`` is a dollar view and is only reachable
    from the Desktop frontend), so this is the one remaining private core import.
    """
    try:
        from agent.account_usage import build_nous_credits_snapshot
        from hermes_cli.nous_account import get_nous_portal_account_info

        # The response cache above this call keeps it to one portal round-trip per two minutes.
        account = get_nous_portal_account_info(force_fresh=True)
        snapshot = build_nous_credits_snapshot(account)
        if snapshot is None:
            return _unavailable(
                "nous",
                "Nous Research",
                "Nous Portal is not connected in this profile. Sign in with `hermes auth`.",
                "nous-portal-account",
            )

        windows = []
        for window in snapshot.windows:
            used = None if window.used_percent is None else max(0.0, min(100.0, float(window.used_percent)))
            windows.append(
                {
                    "label": "Monthly credits" if window.label == "Subscription" else window.label,
                    "used_percent": used,
                    "remaining_percent": None if used is None else max(0.0, 100.0 - used),
                    "reset_at": _iso(window.reset_at),
                    "detail": window.detail,
                    "scope": getattr(window, "scope", "account"),
                }
            )

        details = []
        for raw in snapshot.details:
            text = str(raw)
            if text.startswith("Top up:") or text == "(or run /topup)":
                continue
            details.append(text)

        if not windows and not details:
            return _unavailable(
                "nous",
                "Nous Research",
                "Nous Portal did not return a usable credit balance.",
                "nous-portal-account",
            )

        return {
            "provider": "nous",
            "label": "Nous Research",
            "available": bool(windows or details),
            "plan": snapshot.plan,
            "source": "nous-portal-account",
            "fetched_at": _iso(snapshot.fetched_at),
            "windows": windows,
            "details": details,
            "reason": None,
        }
    except Exception:
        return _unavailable(
            "nous",
            "Nous Research",
            "Nous Portal credit balance could not be queried.",
            "nous-portal-account",
        )


# --- OpenAI Codex -----------------------------------------------------------------------------


def _codex_card() -> dict[str, Any]:
    """Codex through Hermes's own public usage fetcher; the plugin never holds the sign-in.

    Core labels the windows by their published duration (Session / Weekly) and attaches the account
    headers itself. ``read_only=True`` never refreshes or rotates a credential.
    """
    try:
        from agent.account_usage import fetch_account_usage

        return _snapshot_to_card(
            "openai-codex", "OpenAI Codex", fetch_account_usage("openai-codex", read_only=True)
        )
    except Exception:
        return _unavailable(
            "openai-codex",
            "OpenAI Codex",
            "The Codex sign-in could not be queried through Hermes.",
            "hermes-account-usage",
        )


# --- Kimi / Moonshot --------------------------------------------------------------------------

_KIMI_SUB_RE = re.compile(
    r"^\[(?P<at>[^]]+)\].*refreshed\(sub\): level=(?P<level>\d+) "
    r"isMember=(?P<member>true|false) omniRatio=(?P<ratio>[0-9.]+) "
    r"exhausted=(?P<exhausted>true|false) resetAt=(?P<reset>\S+)",
    re.IGNORECASE,
)
_KIMI_PLAN_RE = re.compile(r"commercialInfo sending .*membershipLevel=(?P<plan>\S+)")
_KIMI_STATS_RE = re.compile(r"refreshed\(stats\): overdrawn=(?P<overdrawn>\w+) sendBlocked=(?P<blocked>\w+)")


def _kimi_log_file() -> Path:
    if os.name == "nt":
        roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return roaming / "kimi-desktop" / "logs" / "main.log"
    return Path.home() / ".config" / "kimi-desktop" / "logs" / "main.log"


def _local_log_time(value: str) -> Optional[str]:
    try:
        return datetime.fromisoformat(value.strip()).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _kimi_log_card(fallback_reason: Optional[str] = None) -> dict[str, Any]:
    """Kimi Desktop's own log, read only when the user turned the opt-in setting on."""
    if not _plugin_settings()["kimi_desktop_log_fallback"]:
        reason = fallback_reason or "Kimi usage needs a Kimi Coding key in Hermes."
        return _unavailable(
            "kimi",
            "Kimi / Moonshot",
            f"{reason} Reading the Kimi Desktop log is off; enable it in this plugin's settings.",
            "kimi-coding-usage-api",
        )
    log_path = _kimi_log_file()
    if not log_path.is_file():
        reason = "Kimi Desktop is not installed or has not generated subscription data yet."
        if fallback_reason:
            reason = f"{fallback_reason} {reason}"
        return _unavailable("kimi", "Kimi / Moonshot", reason, "kimi-desktop-log")
    try:
        last_sub = None
        last_plan = None
        last_stats = None
        with log_path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            start = max(0, size - 2_000_000)
            handle.seek(start)
            text = handle.read().decode("utf-8", errors="ignore")
            lines = text.splitlines()
            if start and lines:
                lines = lines[1:]
            for line in lines:
                match = _KIMI_SUB_RE.search(line)
                if match:
                    last_sub = match
                match = _KIMI_PLAN_RE.search(line)
                if match:
                    last_plan = match.group("plan")
                match = _KIMI_STATS_RE.search(line)
                if match:
                    last_stats = match
        if last_sub is None:
            reason = "Open Kimi Desktop and sign in to refresh usage data."
            if fallback_reason:
                reason = f"{fallback_reason} {reason}"
            return _unavailable("kimi", "Kimi / Moonshot", reason, "kimi-desktop-log")
        ratio = max(0.0, min(1.0, float(last_sub.group("ratio"))))
        details = [f"Membership level: {last_sub.group('level')}"]
        if last_stats is not None and last_stats.group("blocked").lower() == "true":
            details.append("Requests are blocked by the provider")
        fetched_at = _local_log_time(last_sub.group("at"))
        stale = True
        if fetched_at:
            try:
                stale = (_now() - datetime.fromisoformat(fetched_at)).total_seconds() > 86_400
            except ValueError:
                stale = True
        if fallback_reason:
            details.append(fallback_reason)
        if stale:
            details.append("Local data is over 24 hours old; open Kimi Desktop to refresh it")
        return {
            "provider": "kimi",
            "label": "Kimi / Moonshot",
            "available": True,
            "plan": last_plan or ("Membership" if last_sub.group("member").lower() == "true" else "Free"),
            "source": "kimi-desktop-log",
            "fetched_at": fetched_at,
            "stale": stale,
            "local": True,
            "windows": [
                {
                    "label": "Subscription period",
                    "used_percent": ratio * 100.0,
                    "remaining_percent": (1.0 - ratio) * 100.0,
                    "reset_at": _normalized_reset(last_sub.group("reset")),
                    "detail": "Local Kimi Desktop data; it refreshes when Kimi is opened.",
                    "scope": "account",
                }
            ],
            "details": details,
            "reason": None,
        }
    except Exception:
        return _unavailable(
            "kimi",
            "Kimi / Moonshot",
            "The local Kimi Desktop state could not be parsed.",
            "kimi-desktop-log",
        )


def _kimi_usage_payload(keys: list[str]) -> Any:
    """GET the Kimi Code usage endpoint with each key in turn. The next key is tried only when the
    provider rejects the previous one as unauthorized (401/403); any other failure is final."""
    with httpx.Client(timeout=10.0) as client:
        for index, token in enumerate(keys):
            response = client.get(
                "https://api.kimi.com/coding/v1/usages",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403) and index + 1 < len(keys):
                    continue
                raise
            return response.json()
    return None


def _kimi_card() -> dict[str, Any]:
    """Kimi Code's own usage endpoint, with an opt-in Kimi Desktop log fallback."""
    try:
        # Keys shaped like a Kimi Code key go first (stable, so KIMI_CODING_API_KEY wins a tie).
        keys = sorted(_declared_secrets(_KIMI_KEY_ENV), key=lambda key: not key.startswith(_KIMI_CODE_KEY_PREFIX))
        if not keys:
            return _kimi_log_card("No Kimi Coding key (KIMI_CODING_API_KEY or KIMI_API_KEY) is available in Hermes.")
        payload = _kimi_usage_payload(keys) or {}
        windows = []

        weekly = payload.get("usage") or {}
        weekly_limit = _number(weekly.get("limit"))
        weekly_used = _number(weekly.get("used"))
        weekly_remaining = _number(weekly.get("remaining"))
        if weekly_limit and (weekly_used is not None or weekly_remaining is not None):
            if weekly_used is None:
                weekly_used = max(0.0, weekly_limit - float(weekly_remaining or 0.0))
            used_percent = max(0.0, min(100.0, weekly_used / weekly_limit * 100.0))
            windows.append(
                {
                    # Kimi Code documents this top-level bucket as weekly.
                    # A monthly quota is never inferred from it.
                    "label": "Weekly quota",
                    "used_percent": used_percent,
                    "remaining_percent": 100.0 - used_percent,
                    "reset_at": _normalized_reset(
                        weekly.get("reset_at")
                        or weekly.get("resetAt")
                        or weekly.get("resetTime")
                    ),
                    "detail": f"{weekly_used:g} used out of {weekly_limit:g}",
                    "scope": "account",
                }
            )

        for index, row in enumerate(payload.get("limits") or []):
            if not isinstance(row, dict):
                continue
            detail = row.get("detail") if isinstance(row.get("detail"), dict) else row
            limit_value = _number(detail.get("limit"))
            used_value = _number(detail.get("used"))
            remaining_value = _number(detail.get("remaining"))
            if not limit_value or (used_value is None and remaining_value is None):
                continue
            if used_value is None:
                used_value = max(0.0, limit_value - float(remaining_value or 0.0))
            used_percent = max(0.0, min(100.0, used_value / limit_value * 100.0))
            window = row.get("window") if isinstance(row.get("window"), dict) else {}
            duration = _number(window.get("duration"))
            unit = str(window.get("timeUnit") or "").upper()
            label = str(row.get("name") or row.get("title") or row.get("scope") or "").strip()
            if duration == 300 and "MINUTE" in unit:
                label = "5-hour window"
            elif not label:
                label = f"Limit {index + 1}"
            windows.append(
                {
                    "label": label,
                    "used_percent": used_percent,
                    "remaining_percent": 100.0 - used_percent,
                    "reset_at": _normalized_reset(
                        detail.get("reset_at")
                        or detail.get("resetAt")
                        or detail.get("resetTime")
                    ),
                    "detail": f"{used_value:g} used out of {limit_value:g}",
                    "scope": "account",
                }
            )

        if not windows:
            return _kimi_log_card("The Kimi API returned no usage limits.")
        membership = ((payload.get("user") or {}).get("membership") or {}).get("level")
        plan = str(membership or "").replace("LEVEL_", "").replace("_", " ").title() or None
        return {
            "provider": "kimi",
            "label": "Kimi / Moonshot",
            "available": True,
            "plan": plan,
            "source": "kimi-coding-usage-api",
            "fetched_at": _iso(_now()),
            "stale": False,
            "windows": windows,
            "details": [],
            "reason": None,
        }
    except httpx.HTTPStatusError as exc:
        return _kimi_log_card(f"The Kimi API rejected the request (HTTP {exc.response.status_code}).")
    except Exception as exc:
        if _is_unscoped_error(exc):
            return _missing_scope_card("kimi", "Kimi / Moonshot", "kimi-coding-usage-api")
        return _kimi_log_card("The Kimi API did not respond.")


# --- Z.ai -------------------------------------------------------------------------------------


def _zai_card() -> dict[str, Any]:
    try:
        token = _first_secret(_ZAI_KEY_ENV)
        if not token:
            return _unavailable(
                "zai",
                "ZCode / Z.ai",
                "No GLM_API_KEY or ZAI_API_KEY was found in Hermes.",
                "zai-quota-api",
            )
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        with httpx.Client(timeout=10.0) as client:
            quota_response = client.get("https://api.z.ai/api/monitor/usage/quota/limit", headers=headers)
            quota_response.raise_for_status()
            quota_payload = quota_response.json() or {}
            try:
                subscription_response = client.get("https://api.z.ai/api/biz/subscription/list", headers=headers)
                subscription_response.raise_for_status()
                subscriptions = subscription_response.json().get("data") or []
            except Exception:
                subscriptions = []

        data = quota_payload.get("data") or {}
        limits = data.get("limits") or []
        windows = []
        for limit in limits:
            if not isinstance(limit, dict):
                continue
            used = limit.get("percentage")
            if not isinstance(used, (int, float)):
                total = limit.get("usage")
                current = limit.get("currentValue")
                can_calculate_percent = (
                    isinstance(total, (int, float))
                    and total > 0
                    and isinstance(current, (int, float))
                )
                used = (
                    float(current) / float(total) * 100.0
                    if can_calculate_percent
                    else None
                )
            if isinstance(used, (int, float)):
                used = max(0.0, min(100.0, float(used)))
            remaining = limit.get("remaining")
            total = limit.get("usage")
            detail_parts = []
            if isinstance(remaining, (int, float)):
                detail_parts.append(f"{remaining:g} remaining")
            if isinstance(total, (int, float)):
                detail_parts.append(f"out of {total:g}")
            if not detail_parts and isinstance(used, (int, float)) and used >= 100.0:
                detail_parts.append("Z.ai reports this quota as fully used")
            limit_type = str(limit.get("type") or "").upper()
            unit = limit.get("unit")
            number = limit.get("number")
            # Enum pairs verified against the local ZCode 3.4 response.
            if limit_type == "TOKENS_LIMIT" and unit == 3 and number == 5:
                window_label = "5-hour window"
            elif limit_type == "TOKENS_LIMIT" and unit == 6 and number == 1:
                window_label = "Weekly window"
            elif limit_type == "TIME_LIMIT" and unit == 5 and number == 1:
                window_label = "Monthly MCP quota"
            else:
                window_label = f"Plan limit ({number or '?'}/{unit or '?'})"
            windows.append(
                {
                    "label": window_label,
                    "used_percent": used,
                    "remaining_percent": None if used is None else max(0.0, 100.0 - used),
                    "reset_at": _epoch_ms_iso(limit.get("nextResetTime")),
                    "detail": " ".join(detail_parts) or None,
                    "scope": "account",
                }
            )

        active = next(
            (
                item for item in subscriptions
                if isinstance(item, dict) and (item.get("inCurrentPeriod") or item.get("status") == "VALID")
            ),
            None,
        )
        plan = (active or {}).get("productName") or data.get("level")
        details = []
        if active:
            if active.get("nextRenewTime"):
                details.append(f"Next renewal: {active['nextRenewTime']}")
            if active.get("billingCycle"):
                details.append(f"Cycle: {active['billingCycle']}")
        return {
            "provider": "zai",
            "label": "ZCode / Z.ai",
            "available": bool(windows),
            "plan": plan,
            "source": "zai-quota-api",
            "fetched_at": _iso(_now()),
            "windows": windows,
            "details": details,
            "reason": None if windows else "Z.ai did not return any limits for this key.",
        }
    except httpx.HTTPStatusError as exc:
        return _unavailable(
            "zai",
            "ZCode / Z.ai",
            f"Z.ai rejected the request (HTTP {exc.response.status_code}).",
            "zai-quota-api",
        )
    except Exception as exc:
        if _is_unscoped_error(exc):
            return _missing_scope_card("zai", "ZCode / Z.ai", "zai-quota-api")
        return _unavailable(
            "zai",
            "ZCode / Z.ai",
            "Z.ai usage could not be queried.",
            "zai-quota-api",
        )


# --- Contract 1: availability.json ------------------------------------------------------------
# Published for the other Agent OS plugins (routes, aiFactory) and the `usage_availability` tool.
# Facts only: no tokens, accounts or e-mail addresses. Read-only for consumers.

AVAILABILITY_FILE = "availability.json"
AVAILABILITY_STALE_SECONDS = 900.0
_AVAILABILITY_WINDOW_NAMES = {0: "session", 1: "week", 2: "month", 3: "other"}
_EXHAUSTED_AT_OR_BELOW = 1.0


def _utc_z(moment: Optional[datetime]) -> Optional[str]:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def _unknown_entry(stale: bool = True) -> dict[str, Any]:
    return {"state": "unknown", "remaining_percent": None, "window": None, "reset_at": None, "stale": stale}


def _availability_entry(card: Any, settings: dict[str, Any], now: datetime) -> dict[str, Any]:
    """One provider's state. Model-scoped windows never decide it; the session (5 h) and weekly
    windows decide it when present, otherwise whatever account-wide windows the provider has."""
    if not isinstance(card, dict) or not card.get("available"):
        return _unknown_entry()
    fetched = _parse_dt(card.get("fetched_at"))
    if (
        card.get("stale")
        or fetched is None
        or (now - fetched).total_seconds() > AVAILABILITY_STALE_SECONDS
    ):
        return _unknown_entry()
    candidates: list[tuple[dict[str, Any], float]] = []
    for window in card.get("windows") or []:
        if not isinstance(window, dict) or window.get("scope") == "model":
            continue
        remaining = window.get("remaining_percent")
        if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
            candidates.append((window, float(remaining)))
    if not candidates:
        return _unknown_entry(stale=False)
    primary = [item for item in candidates if _window_order(item[0]) <= 1] or candidates
    exhausted = [item for item in primary if item[1] <= _EXHAUSTED_AT_OR_BELOW]
    if exhausted:
        # Access returns only when the LAST exhausted window resets; an unknown reset outranks a known one.
        def blocks_longest(item: tuple[dict[str, Any], float]) -> tuple[bool, float, int]:
            reset = _parse_dt(item[0].get("reset_at"))
            return (reset is None, reset.timestamp() if reset else 0.0, -_window_order(item[0]))

        chosen = max(exhausted, key=blocks_longest)
        state = "exhausted"
    else:
        chosen = min(primary, key=lambda item: (item[1], _window_order(item[0])))
        state = "low" if chosen[1] <= settings["low_threshold_percent"] else "ok"
    window, remaining = chosen
    return {
        "state": state,
        "remaining_percent": max(0, min(100, int(round(remaining)))),
        "window": _AVAILABILITY_WINDOW_NAMES[_window_order(window)],
        "reset_at": _utc_z(_parse_dt(window.get("reset_at"))),
        "stale": False,
    }


def _availability_document(cards: list[dict[str, Any]], settings: dict[str, Any], now: datetime) -> dict[str, Any]:
    by_id = {card.get("provider"): card for card in cards if isinstance(card, dict)}
    return {
        "schema": 1,
        "generated_at": _utc_z(now),
        "providers": {
            provider_id: _availability_entry(by_id.get(card_id), settings, now)
            for card_id, _label, provider_id in _PROVIDERS
        },
    }


def _write_availability(document: dict[str, Any]) -> None:
    """Atomic write next to the plugin's other data; a failure never breaks the dashboard."""
    try:
        path = _data_dir() / AVAILABILITY_FILE
        temporary = path.with_name(f"availability.{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except Exception:
        logger.debug("could not write %s", AVAILABILITY_FILE, exc_info=True)


# --- Collection and route ---------------------------------------------------------------------


def _public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "alerts_enabled": bool(settings["alerts_enabled"]),
        "alert_warn_percent": settings["alert_warn_percent"],
        "alert_critical_percent": settings["alert_critical_percent"],
    }


def _collect() -> dict[str, Any]:
    jobs = {
        "openai-codex": _codex_card,
        "anthropic": _anthropic_card,
        "kimi": _kimi_card,
        "zai": _zai_card,
        "nous": _nous_card,
    }
    cards: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=5, thread_name_prefix="usage-dashboard") as pool:
        futures = {pool.submit(copy_context().run, fn): key for key, fn in jobs.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                cards[key] = future.result()
            except Exception:
                cards[key] = _unavailable(key, key, "Unexpected error while querying this provider.")

    ordered = [_public_card(cards[card_id]) for card_id, _label, _provider in _PROVIDERS]
    for card in ordered:
        windows = card.get("windows")
        if isinstance(windows, list):
            card["windows"] = sorted(windows, key=_window_order)
    remaining_values = [
        window["remaining_percent"]
        for card in ordered
        if card.get("available")
        for window in card.get("windows", [])
        if window.get("scope") != "model" and isinstance(window.get("remaining_percent"), (int, float))
    ]
    settings = _plugin_settings()
    now = _now()
    _write_availability(_availability_document(ordered, settings, now))
    return {
        "fetched_at": _iso(now),
        "providers": ordered,
        "summary": {
            "available": sum(1 for card in ordered if card.get("available")),
            "total": len(ordered),
            "lowest_remaining_percent": min(remaining_values) if remaining_values else None,
        },
        "settings": _public_settings(settings),
        "privacy": "Credentials are used only by the local backend and are never sent to the interface.",
    }


def _missing_scope_payload() -> dict[str, Any]:
    cards = [_public_card(_missing_scope_card(card_id, label)) for card_id, label, _provider in _PROVIDERS]
    return {
        "fetched_at": _iso(_now()),
        "providers": cards,
        "summary": {"available": 0, "total": len(cards), "lowest_remaining_percent": None},
        "settings": _public_settings(_plugin_settings()),
        "privacy": "Credentials are used only by the local backend and are never sent to the interface.",
    }


def _scope_mode() -> str:
    """How the current request is bound by Hermes (>= 0.22 binds a secret scope on plugin routes).

    ``launch``: the launch profile's own scope, so process-level caches are safe.
    ``routed``: another profile's scope, so no cache from the launch profile may be reused.
    ``missing``: no scope. The plugin never binds one itself; it reports ``missing_profile_scope``.
    """
    try:
        from agent.secret_scope import current_secret_scope
        from hermes_constants import get_hermes_home, get_process_hermes_home
    except ImportError:
        return "missing"
    if current_secret_scope() is None:
        return "missing"
    return "launch" if get_hermes_home().resolve() == get_process_hermes_home().resolve() else "routed"


@router.get("/usage")
def get_usage():
    mode = _scope_mode()
    if mode == "missing":
        return _missing_scope_payload()
    if mode == "routed":
        # A routed (foreign-home) scope is another account: never reuse launch responses or caches.
        token = _USE_PROCESS_CACHE.set(False)
        try:
            return _collect()
        finally:
            _USE_PROCESS_CACHE.reset(token)
    return _get_usage()


def _response_cache_is_fresh(now: float) -> bool:
    cached = _CACHE.get("payload")
    return (
        cached is not None
        and now - float(_CACHE.get("at") or 0.0) < _CACHE_TTL_SECONDS
        and not _payload_has_rolled_over(cached)
    )


def _get_usage():
    if not _USE_PROCESS_CACHE.get():
        return _collect()
    now = time.monotonic()
    with _CACHE_LOCK:
        if _response_cache_is_fresh(now):
            return _CACHE["payload"]
    with _COLLECT_LOCK:
        now = time.monotonic()
        with _CACHE_LOCK:
            if _response_cache_is_fresh(now):
                return _CACHE["payload"]
        payload = _collect()
        with _CACHE_LOCK:
            _CACHE["at"] = time.monotonic()
            _CACHE["payload"] = payload
        return payload
