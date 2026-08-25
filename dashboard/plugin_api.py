"""Backend for the Hermes Desktop subscription-usage dashboard.

Only sanitized usage data leaves this module. OAuth/API credentials remain in
Hermes's provider resolvers and are never included in responses or logs.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter

router = APIRouter()


def _hermes_root() -> Path:
    configured = os.environ.get("HERMES_HOME")
    if configured:
        home = Path(configured).expanduser()
        if home.parent.name == "profiles":
            return home.parent.parent
        return home
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "hermes"
    return Path.home() / ".hermes"


def _kimi_log_file() -> Path:
    if os.name == "nt":
        roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return roaming / "kimi-desktop" / "logs" / "main.log"
    return Path.home() / ".config" / "kimi-desktop" / "logs" / "main.log"

_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 120.0
_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
_COLLECT_LOCK = threading.Lock()
_ANTHROPIC_CACHE_FILE = _hermes_root() / "cache" / "usage-dashboard" / "anthropic.json"
_ANTHROPIC_FRESH_SECONDS = 300.0
_ANTHROPIC_STALE_SECONDS = 86_400.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.astimezone(timezone.utc).isoformat() if value else None


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


def _snapshot_card(provider: str, label: str) -> dict[str, Any]:
    from agent.account_usage import fetch_account_usage

    return _snapshot_to_card(provider, label, fetch_account_usage(provider))


def _read_anthropic_cache(max_age: float) -> Optional[dict[str, Any]]:
    try:
        entry = json.loads(_ANTHROPIC_CACHE_FILE.read_text(encoding="utf-8"))
        saved_at = float(entry.get("saved_at") or 0.0)
        card = entry.get("card")
        if time.time() - saved_at > max_age or not isinstance(card, dict):
            return None
        if card.get("provider") != "anthropic" or not isinstance(card.get("windows"), list):
            return None
        return json.loads(json.dumps(card))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_anthropic_cache(card: dict[str, Any]) -> None:
    if not card.get("available") or not card.get("windows"):
        return
    try:
        _ANTHROPIC_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = _ANTHROPIC_CACHE_FILE.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"saved_at": time.time(), "card": card}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(_ANTHROPIC_CACHE_FILE)
    except OSError:
        pass


def _anthropic_card() -> dict[str, Any]:
    fresh = _read_anthropic_cache(_ANTHROPIC_FRESH_SECONDS)
    if fresh is not None:
        fresh["source"] = "oauth_usage_api-cache"
        return fresh

    failure_reason = "Claude Code usage could not be queried temporarily."
    try:
        from agent.account_usage import _fetch_anthropic_account_usage

        card = _snapshot_to_card("anthropic", "Claude Code", _fetch_anthropic_account_usage())
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
    except Exception:
        pass

    stale = _read_anthropic_cache(_ANTHROPIC_STALE_SECONDS)
    if stale is not None:
        stale["source"] = "oauth_usage_api-cache"
        stale["stale"] = True
        stale["details"] = list(stale.get("details") or []) + [failure_reason]
        return stale
    return _unavailable("anthropic", "Claude Code", failure_reason, "oauth_usage_api")


def _nous_card() -> dict[str, Any]:
    """Fetch the signed-in Nous Portal account and expose only credit totals."""
    try:
        from agent.account_usage import build_nous_credits_snapshot
        from hermes_cli.nous_account import get_nous_portal_account_info

        # Hermes performs the account HTTP fetch with an internal 8-second
        # timeout; snapshot construction is local and does no network I/O.
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


def _codex_card() -> dict[str, Any]:
    """Read the same local OAuth-backed usage endpoint Hermes uses.

    Hermes's generic snapshot currently labels ``primary_window`` as a
    session. Codex can place a weekly bucket there, so classify by the actual
    duration instead of by field position.
    """
    try:
        try:
            from agent.account_usage import _resolve_codex_usage_credentials, _resolve_codex_usage_url
        except (ImportError, AttributeError):
            return _snapshot_card("openai-codex", "OpenAI Codex")

        token, base_url, account_id = _resolve_codex_usage_credentials(None, None)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "codex-cli",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        with httpx.Client(timeout=15.0) as client:
            response = client.get(_resolve_codex_usage_url(base_url), headers=headers)
            response.raise_for_status()
        payload = response.json() or {}
        rate_limit = payload.get("rate_limit") or {}
        windows = []
        for key in ("primary_window", "secondary_window"):
            window = rate_limit.get(key) or {}
            used = window.get("used_percent")
            if not isinstance(used, (int, float)):
                continue
            duration_seconds = window.get("limit_window_seconds")
            if duration_seconds == 18_000:
                label = "5-hour window"
            elif duration_seconds == 604_800:
                label = "Weekly window"
            elif isinstance(duration_seconds, (int, float)) and 2_419_200 <= duration_seconds <= 2_678_400:
                label = "Monthly period"
            else:
                label = "Codex limit"
            used_percent = max(0.0, min(100.0, float(used)))
            windows.append(
                {
                    "label": label,
                    "used_percent": used_percent,
                    "remaining_percent": 100.0 - used_percent,
                    "reset_at": _epoch_ms_iso(window.get("reset_at")),
                    "detail": None,
                }
            )
        return {
            "provider": "openai-codex",
            "label": "OpenAI Codex",
            "available": bool(windows),
            "plan": str(payload.get("plan_type") or "").title() or None,
            "source": "codex-usage-api",
            "fetched_at": _iso(_now()),
            "windows": windows,
            "details": [],
            "reason": None if windows else "Codex did not return any usage windows.",
        }
    except TypeError:
        return _snapshot_card("openai-codex", "OpenAI Codex")
    except Exception:
        fallback = _snapshot_card("openai-codex", "OpenAI Codex")
        if fallback.get("available"):
            return fallback
        return _unavailable(
            "openai-codex",
            "OpenAI Codex",
            "The local Codex OAuth session could not be queried.",
            "codex-usage-api",
        )


_KIMI_SUB_RE = re.compile(
    r"^\[(?P<at>[^]]+)\].*refreshed\(sub\): level=(?P<level>\d+) "
    r"isMember=(?P<member>true|false) omniRatio=(?P<ratio>[0-9.]+) "
    r"exhausted=(?P<exhausted>true|false) resetAt=(?P<reset>\S+)",
    re.IGNORECASE,
)
_KIMI_PLAN_RE = re.compile(r"commercialInfo sending .*membershipLevel=(?P<plan>\S+)")
_KIMI_STATS_RE = re.compile(r"refreshed\(stats\): overdrawn=(?P<overdrawn>\w+) sendBlocked=(?P<blocked>\w+)")


def _local_log_time(value: str) -> Optional[str]:
    try:
        return datetime.fromisoformat(value.strip()).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _kimi_log_card(fallback_reason: Optional[str] = None) -> dict[str, Any]:
    log_path = _kimi_log_file()
    if not log_path.is_file():
        reason = "Kimi Desktop is not installed or has not generated subscription data yet."
        if fallback_reason:
            reason = f"{fallback_reason} {reason}"
        return _unavailable(
            "kimi",
            "Kimi / Moonshot",
            reason,
            "kimi-desktop-log",
        )
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
            return _unavailable(
                "kimi",
                "Kimi / Moonshot",
                reason,
                "kimi-desktop-log",
            )
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


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_reset(value: Any) -> Optional[str]:
    if isinstance(value, (int, float)):
        return _epoch_ms_iso(value)
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except ValueError:
            return None
    return None


def _kimi_card() -> dict[str, Any]:
    """Read Kimi Code's own usage endpoint, with Desktop log fallback."""
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested="kimi-coding")
        token = str(runtime.get("api_key") or "").strip()
        if not token:
            return _kimi_log_card("No Kimi Coding key is available; showing local data instead.")
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                "https://api.kimi.com/coding/v1/usages",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            response.raise_for_status()
        payload = response.json() or {}
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
                }
            )

        if not windows:
            return _kimi_log_card()
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
        return _kimi_log_card(
            f"The Kimi API rejected the request (HTTP {exc.response.status_code}); showing local data instead."
        )
    except Exception:
        return _kimi_log_card("The Kimi API did not respond; showing local data instead.")


def _epoch_ms_iso(value: Any) -> Optional[str]:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        seconds = float(value) / 1000.0 if value > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _zai_card() -> dict[str, Any]:
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested="zai")
        token = str(runtime.get("api_key") or "").strip()
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
        for index, limit in enumerate(limits):
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
            limit_type = str(limit.get("type") or "").upper()
            unit = limit.get("unit")
            number = limit.get("number")
            # Enum pairs verified against the local ZCode 3.4 response.
            if limit_type == "TOKENS_LIMIT" and unit == 3 and number == 5:
                window_label = "5-hour window"
            elif limit_type == "TOKENS_LIMIT" and unit == 6 and number == 1:
                window_label = "Weekly window"
            elif limit_type == "TIME_LIMIT":
                window_label = "Subscription period"
            else:
                window_label = f"Plan limit ({number or '?'}/{unit or '?'})"
            windows.append(
                {
                    "label": window_label,
                    "used_percent": used,
                    "remaining_percent": None if used is None else max(0.0, 100.0 - used),
                    "reset_at": _epoch_ms_iso(limit.get("nextResetTime")),
                    "detail": " ".join(detail_parts) or None,
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
    except Exception:
        return _unavailable(
            "zai",
            "ZCode / Z.ai",
            "Z.ai usage could not be queried.",
            "zai-quota-api",
        )


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
        futures = {pool.submit(fn): key for key, fn in jobs.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                cards[key] = future.result()
            except Exception:
                cards[key] = _unavailable(key, key, "Unexpected error while querying this provider.")

    ordered = [cards[key] for key in ("openai-codex", "anthropic", "kimi", "zai", "nous")]
    remaining_values = [
        window["remaining_percent"]
        for card in ordered
        if card.get("available")
        for window in card.get("windows", [])
        if isinstance(window.get("remaining_percent"), (int, float))
    ]
    return {
        "fetched_at": _iso(_now()),
        "providers": ordered,
        "summary": {
            "available": sum(1 for card in ordered if card.get("available")),
            "total": len(ordered),
            "lowest_remaining_percent": min(remaining_values) if remaining_values else None,
        },
        "privacy": "Credentials are used only by the local backend and are never sent to the interface.",
    }


@router.get("/usage")
def get_usage():
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get("payload")
        if cached is not None and now - float(_CACHE.get("at") or 0.0) < _CACHE_TTL_SECONDS:
            return cached
    with _COLLECT_LOCK:
        now = time.monotonic()
        with _CACHE_LOCK:
            cached = _CACHE.get("payload")
            if cached is not None and now - float(_CACHE.get("at") or 0.0) < _CACHE_TTL_SECONDS:
                return cached
        payload = _collect()
        with _CACHE_LOCK:
            _CACHE["at"] = time.monotonic()
            _CACHE["payload"] = payload
        return payload
