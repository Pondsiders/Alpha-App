"""Tests for find_circadian_chat — the circadian day boundary logic.

The circadian day runs 6 AM to 6 AM, not midnight to midnight.
A chat created at 3 PM on March 31 still belongs to that circadian day
at 1 AM on April 1. These tests verify the boundary doesn't break at midnight.
"""

import time

import pendulum
import pytest

from alpha_app.chat import Chat, find_circadian_chat


def _make_chat(id: str, created_at: float, updated_at: float | None = None) -> Chat:
    """Create a minimal Chat with specific timestamps."""
    chat = Chat(id=id)
    chat.created_at = created_at
    chat.updated_at = updated_at or created_at
    return chat


def _ts(time_str: str) -> float:
    """Parse a local time string to a unix timestamp.

    E.g. _ts("2026-03-31 15:00") -> float
    """
    return pendulum.parse(time_str, tz="America/Los_Angeles").timestamp()


class TestCircadianDayBoundary:
    """The critical boundary: midnight should NOT reset the circadian day."""

    def test_chat_found_before_midnight(self):
        """At 11 PM, a chat created at 3 PM today is found."""
        chat = _make_chat("abc", created_at=_ts("2026-03-31 15:00"))
        chats = {"abc": chat}
        result = find_circadian_chat(chats, now=_ts("2026-03-31 23:00"))
        assert result is chat

    def test_chat_found_after_midnight(self):
        """At 1 AM April 1, a chat created at 3 PM March 31 is still found.
        THIS IS THE BUG THAT KILLED THE OUROBOROS."""
        chat = _make_chat("abc", created_at=_ts("2026-03-31 15:00"))
        chats = {"abc": chat}
        result = find_circadian_chat(chats, now=_ts("2026-04-01 01:00"))
        assert result is chat

    def test_chat_found_at_5am(self):
        """At 5 AM, a chat from yesterday's circadian day is still found."""
        chat = _make_chat("abc", created_at=_ts("2026-03-31 08:00"))
        chats = {"abc": chat}
        result = find_circadian_chat(chats, now=_ts("2026-04-01 05:59"))
        assert result is chat

    def test_chat_not_found_at_6am(self):
        """At 6 AM, yesterday's chat is no longer in the circadian day.
        The new day has started — Dawn creates a fresh chat."""
        chat = _make_chat("abc", created_at=_ts("2026-03-31 08:00"))
        chats = {"abc": chat}
        result = find_circadian_chat(chats, now=_ts("2026-04-01 06:00"))
        assert result is None


class TestSolitudeExclusion:
    """The 'solitude' chat ID is always excluded."""

    def test_solitude_excluded(self):
        solitude = _make_chat("solitude", created_at=_ts("2026-03-31 22:00"))
        real = _make_chat("abc", created_at=_ts("2026-03-31 08:00"))
        chats = {"solitude": solitude, "abc": real}
        result = find_circadian_chat(chats, now=_ts("2026-03-31 23:00"))
        assert result is real

    def test_only_solitude_returns_none(self):
        solitude = _make_chat("solitude", created_at=_ts("2026-03-31 22:00"))
        chats = {"solitude": solitude}
        result = find_circadian_chat(chats, now=_ts("2026-03-31 23:00"))
        assert result is None


class TestMostRecentWins:
    """When multiple chats exist, the most recently updated wins."""

    def test_most_recent_updated(self):
        old = _make_chat("old", created_at=_ts("2026-03-31 07:00"), updated_at=_ts("2026-03-31 09:00"))
        new = _make_chat("new", created_at=_ts("2026-03-31 10:00"), updated_at=_ts("2026-03-31 18:00"))
        chats = {"old": old, "new": new}
        result = find_circadian_chat(chats, now=_ts("2026-03-31 23:00"))
        assert result is new


class TestEmptyChats:
    """Edge cases with no chats."""

    def test_empty_dict(self):
        result = find_circadian_chat({}, now=_ts("2026-03-31 12:00"))
        assert result is None

    def test_all_chats_from_previous_day(self):
        """Chat created two days ago doesn't match."""
        old = _make_chat("old", created_at=_ts("2026-03-29 12:00"))
        chats = {"old": old}
        result = find_circadian_chat(chats, now=_ts("2026-03-31 12:00"))
        assert result is None
