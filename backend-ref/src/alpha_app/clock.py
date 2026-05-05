"""clock.py — Pondside timekeeping. Days start at 6 AM.

A Pondside day runs from 6 AM to 6 AM. At 3 AM on April 9, it's still
April 8 in Pondside time. This module provides the canonical boundary
calculations so nobody has to reimplement them inline.

Timezone comes from the TZ environment variable (set in compose.yml).
No hardcoded timezone strings.

Usage:
    from alpha_app.clock import now, today_dawn, yesterday_dawn, pondside_date
"""

from __future__ import annotations

import datetime as _dt
import json as _json

import pendulum
from starlette.responses import JSONResponse

# The hour the Pondside day begins. Everything before this belongs
# to the previous day.
DAWN_HOUR = 6


def now() -> pendulum.DateTime:
    """Current time in the local timezone (from TZ env var)."""
    return pendulum.now()


def today_dawn() -> pendulum.DateTime:
    """Start of today's Pondside day (most recent 6 AM)."""
    n = now()
    if n.hour >= DAWN_HOUR:
        return n.replace(hour=DAWN_HOUR, minute=0, second=0, microsecond=0)
    return n.subtract(days=1).replace(hour=DAWN_HOUR, minute=0, second=0, microsecond=0)


def yesterday_dawn() -> pendulum.DateTime:
    """Start of yesterday's Pondside day."""
    return today_dawn().subtract(days=1)


def tomorrow_dawn() -> pendulum.DateTime:
    """Start of tomorrow's Pondside day."""
    return today_dawn().add(days=1)


def pso_timestamp(dt: pendulum.DateTime | None = None) -> str:
    """Format a datetime as PSO-8601: 'Sun Apr 12 2026, 2:39 PM'.

    Always converts to local time first. UTC datetimes from Postgres
    become Pondside time. This is the ONE place where tz conversion
    happens on the way to a string.
    """
    dt = dt or now()
    if not isinstance(dt, pendulum.DateTime):
        dt = pendulum.instance(dt)
    # Convert to local timezone (from TZ env var)
    dt = dt.in_tz(pendulum.now().timezone)
    return dt.format("ddd MMM D YYYY, h:mm A")


def pso_date(dt: pendulum.DateTime | pendulum.Date | None = None) -> str:
    """Format a date as PSO-8601: 'Sun Apr 12 2026'."""
    dt = dt or now()
    if isinstance(dt, pendulum.DateTime):
        return dt.format("ddd MMM D YYYY")
    return pendulum.instance(dt).format("ddd MMM D YYYY")


def pso_time(t) -> str:
    """Format a time as PSO-8601: '10:00 PM'."""
    if hasattr(t, "hour"):
        h = t.hour
        m = t.minute
        period = "AM" if h < 12 else "PM"
        display_h = h % 12 or 12
        return f"{display_h}:{m:02d} {period}"
    return str(t)


def count_tokens(text: str) -> int:
    """Count tokens using Anthropic's token-counting endpoint.

    Requires ANTHROPIC_API_KEY in the environment. The endpoint is free;
    the key is for rate limiting. Returns the exact token count.
    """
    import os
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Fallback: rough estimate if no key available
        return len(text.split()) * 2

    client = anthropic.Anthropic(api_key=api_key)
    result = client.messages.count_tokens(
        model="claude-opus-4-6",
        messages=[{"role": "user", "content": text}],
    )
    return result.input_tokens


class PSOResponse(JSONResponse):
    """JSON response with PSO-8601 formatted datetimes.

    Returns a Response directly, bypassing FastAPI's jsonable_encoder.
    Datetimes stay as objects until this render() formats them.
    """

    def render(self, content) -> bytes:
        def _default(obj):
            if isinstance(obj, _dt.datetime):
                return pso_timestamp(pendulum.instance(obj))
            if isinstance(obj, _dt.date):
                return pso_date(obj)
            if isinstance(obj, _dt.time):
                return pso_time(obj)
            raise TypeError(f"Not JSON serializable: {type(obj)}")

        return _json.dumps(content, default=_default, ensure_ascii=False).encode("utf-8")


def pondside_date(dt: pendulum.DateTime | None = None) -> pendulum.Date:
    """The Pondside date a timestamp belongs to.

    3 AM on April 9 is still April 8 in Pondside time.
    """
    dt = dt or now()
    if dt.hour < DAWN_HOUR:
        return dt.subtract(days=1).date()
    return dt.date()
