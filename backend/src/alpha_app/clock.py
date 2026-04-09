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

import pendulum

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


def pondside_date(dt: pendulum.DateTime | None = None) -> pendulum.Date:
    """The Pondside date a timestamp belongs to.

    3 AM on April 9 is still April 8 in Pondside time.
    """
    dt = dt or now()
    if dt.hour < DAWN_HOUR:
        return dt.subtract(days=1).date()
    return dt.date()
