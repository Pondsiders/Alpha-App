"""Tests for suggest.py — the post-turn reminder."""

from __future__ import annotations

from alpha_app.suggest import POST_TURN_REMINDER


class TestPostTurnReminder:
    def test_is_nonempty_string(self):
        assert isinstance(POST_TURN_REMINDER, str)
        assert len(POST_TURN_REMINDER) > 0

    def test_wrapped_in_system_reminder_tags(self):
        """The reminder must use the <system-reminder> convention so the
        model treats it as ambient context, not a conversational turn."""
        assert POST_TURN_REMINDER.startswith("<system-reminder>")
        assert POST_TURN_REMINDER.rstrip().endswith("</system-reminder>")

    def test_names_the_store_tool(self):
        """The reminder must tell Alpha to call store — that's the whole point."""
        assert "store tool" in POST_TURN_REMINDER

    def test_gives_explicit_null_case_permission(self):
        """The reminder must say finding nothing is a legitimate outcome.
        Without this, the model manufactures significance where none exists."""
        assert "correct outcome" in POST_TURN_REMINDER

    def test_reminds_conversation_is_still_waiting(self):
        """The reminder must not be mistaken for Jeffery's reply — the
        conversation is still waiting on his actual next message."""
        assert "still waiting" in POST_TURN_REMINDER

    def test_identifies_source_as_alpha_app_not_jeffery(self):
        """Voice channel separation: the reminder must name itself as
        system voice so Alpha doesn't treat it as user input."""
        assert "not from Jeffery" in POST_TURN_REMINDER

    def test_anti_fourth_wall_clause(self):
        """Alpha must not reference the reminder in her reply to Jeffery."""
        assert "Do not reference this reminder" in POST_TURN_REMINDER

    def test_contains_no_prohibition_on_generating_text(self):
        """Explicitly verify the silence-shaped-void antipattern is absent.
        Phrases like 'do not respond' create a silence the model must fill,
        which is the failure mode this redesign is correcting."""
        lowered = POST_TURN_REMINDER.lower()
        assert "do not respond" not in lowered
        assert "do not acknowledge" not in lowered
        assert "do not produce any text" not in lowered
        assert "produce no output" not in lowered
