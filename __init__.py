"""AI Usage (usage-dashboard) agent plugin.

The Desktop status-bar chip and its usage route live in ``desktop/plugin.js`` and
``dashboard/plugin_api.py``. This module adds the agent-facing side: the ``usage_availability`` tool
and the ``/usage-availability`` command, both reading ``availability.json`` read-only.

The sibling module is imported inside the functions so this file also imports cleanly as a
top-level module (a test collector does that); Hermes loads it as a package.
"""
from __future__ import annotations

import json

USAGE_AVAILABILITY_SCHEMA = {
    "name": "usage_availability",
    "description": (
        "Report which AI subscriptions (Claude Code, OpenAI Codex, Kimi, Z.ai, Nous) still have quota "
        "and when an exhausted one comes back. Use it before choosing which provider, model or external "
        "agent should take a long or expensive task, or when the user asks what quota is left. "
        "States: ok, low, exhausted, unknown. Unknown means no recent data and must not block anything."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def _usage_availability(args=None, **kwargs) -> str:
    """Tool handler: JSON string with a text ``summary`` plus the sanitized per-provider facts."""
    try:
        from . import availability

        document = availability.load()
        return json.dumps(
            {
                "summary": availability.format_summary(document),
                "generated_at": document["generated_at"],
                "providers": document["providers"],
            },
            ensure_ascii=False,
        )
    except Exception:
        return json.dumps({"error": "Quota availability could not be read."})


def _usage_availability_command(raw_args: str = "") -> str:
    try:
        from . import availability

        return availability.format_summary(availability.load())
    except Exception:
        return "Quota availability could not be read."


def register(ctx):
    ctx.register_tool(
        name="usage_availability",
        toolset="usage-dashboard",
        schema=USAGE_AVAILABILITY_SCHEMA,
        handler=_usage_availability,
        description="Which AI subscriptions still have quota",
        emoji="📊",
    )
    # Hermes 0.23 has no `usage_extra` hook for /usage, so the same summary is also a slash command.
    ctx.register_command(
        "usage-availability",
        _usage_availability_command,
        description="Show which AI subscriptions still have quota",
    )
