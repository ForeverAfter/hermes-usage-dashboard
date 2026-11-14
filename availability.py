"""Reader side of the availability contract (``availability.json``).

The dashboard backend writes ``availability.json`` into this plugin's data directory every time it
collects usage. This module reads it, read-only, for the ``usage_availability`` tool and the
``/usage-availability`` command. Anything missing, corrupt, older than 15 minutes or stamped more than
a minute in the future reads as ``unknown``: availability is an optimization, never a gate.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PLUGIN_NAME = "usage-dashboard"
FILE_NAME = "availability.json"
STALE_SECONDS = 900.0
FUTURE_TOLERANCE_SECONDS = 60.0  # clock skew accepted on generated_at; beyond it the file reads as unknown
STATES = ("ok", "low", "exhausted", "unknown")
PROVIDER_IDS = ("anthropic", "openai-codex", "kimi-coding", "zai", "nous")


def availability_path() -> Path:
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME) / FILE_NAME


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _unknown() -> dict[str, Any]:
    return {"state": "unknown", "remaining_percent": None, "window": None, "reset_at": None, "stale": True}


def _valid_percent(value: Any) -> bool:
    """``None`` or a finite number in 0-100. Booleans, strings, infinities and NaN are not percentages."""
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and 0 <= value <= 100


def _clean_entry(raw: Any) -> dict[str, Any]:
    """Keep only the contract's fields, with the contract's types. Anything malformed is ``unknown``."""
    try:
        if not isinstance(raw, dict) or raw.get("state") not in STATES:
            return _unknown()
        remaining = raw.get("remaining_percent")
        if not _valid_percent(remaining):
            return _unknown()
        return {
            "state": raw["state"],
            "remaining_percent": None if remaining is None else int(remaining),
            "window": raw.get("window") if isinstance(raw.get("window"), str) else None,
            "reset_at": raw.get("reset_at") if isinstance(raw.get("reset_at"), str) else None,
            "stale": bool(raw.get("stale")),
        }
    except Exception:  # a single corrupt entry must never take the whole document down
        return _unknown()


def load(path: Optional[Path] = None, now: Optional[datetime] = None) -> dict[str, Any]:
    """Sanitized availability document. Never raises; missing or old data reads as ``unknown``."""
    now = now or datetime.now(timezone.utc)
    document: dict[str, Any] = {
        "schema": 1,
        "generated_at": None,
        "age_seconds": None,
        "providers": {provider_id: _unknown() for provider_id in PROVIDER_IDS},
    }
    try:
        raw = json.loads((path or availability_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return document
    if not isinstance(raw, dict) or raw.get("schema") != 1:
        return document
    generated = _parse(raw.get("generated_at"))
    if generated is None:
        return document
    age = (now - generated).total_seconds()
    document["generated_at"] = raw["generated_at"]
    if age < -FUTURE_TOLERANCE_SECONDS:
        return document  # stamped in the future: a wrong clock, not fresh data (age_seconds stays None)
    document["age_seconds"] = max(0, int(age))
    if age > STALE_SECONDS:
        return document  # too old to trust: every provider stays unknown
    providers = raw.get("providers") if isinstance(raw.get("providers"), dict) else {}
    for provider_id, entry in providers.items():
        if isinstance(provider_id, str):
            document["providers"][provider_id] = _clean_entry(entry)
    return document


def _until(reset_at: Optional[str], now: datetime) -> str:
    moment = _parse(reset_at)
    if moment is None:
        return ""
    seconds = int((moment - now).total_seconds())
    if seconds <= 0:
        return f", reset time {reset_at} has passed"
    hours, minutes = divmod(seconds // 60, 60)
    span = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
    return f", resets {reset_at} (in {span})"


def format_summary(document: dict[str, Any], now: Optional[datetime] = None) -> str:
    """Plain-text summary: which subscriptions have quota and when exhausted ones come back."""
    now = now or datetime.now(timezone.utc)
    providers = document.get("providers") or {}
    if document.get("generated_at") is None:
        return (
            "No quota data yet: the AI Usage dashboard has not collected on this profile "
            "(it refreshes while Hermes Desktop is open). Treat every provider as unknown and do not block on this."
        )
    lines = [f"Subscription quota as of {document['generated_at']}:"]
    ordered = [key for key in PROVIDER_IDS if key in providers]
    ordered += sorted(key for key in providers if key not in PROVIDER_IDS)
    for provider_id in ordered:
        entry = providers[provider_id]
        state = entry["state"]
        if state == "unknown":
            lines.append(f"- {provider_id}: unknown (no recent data)")
            continue
        window = f" ({entry['window']})" if entry.get("window") else ""
        remaining = "?" if entry["remaining_percent"] is None else f"{entry['remaining_percent']}%"
        label = "EXHAUSTED" if state == "exhausted" else state
        lines.append(f"- {provider_id}: {label} - {remaining} left{window}{_until(entry.get('reset_at'), now)}")
    if document.get("age_seconds") is None:
        lines.append("The data is timestamped in the future (clock change?), so every provider is reported as unknown.")
    elif document["age_seconds"] > STALE_SECONDS:
        lines.append("The data is older than 15 minutes, so every provider is reported as unknown.")
    else:
        lines.append("Prefer providers marked ok; avoid exhausted ones until their reset time.")
    return "\n".join(lines)
