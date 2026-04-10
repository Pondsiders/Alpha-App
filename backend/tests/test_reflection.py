"""Tests for reflection.py — the post-turn reminder."""

from __future__ import annotations

from alpha_app.reflection import REFLECTION_REMINDER, build_reflection_reminder


class TestReflectionReminder:
    def test_is_nonempty_string(self):
        assert isinstance(REFLECTION_REMINDER, str)
        assert len(REFLECTION_REMINDER) > 0

    def test_wrapped_in_system_reminder_tags(self):
        """The reminder must use the <system-reminder> convention so the
        model treats it as ambient context, not a conversational turn."""
        assert REFLECTION_REMINDER.startswith("<system-reminder>")
        assert REFLECTION_REMINDER.rstrip().endswith("</system-reminder>")

    def test_names_the_store_tool(self):
        """The reminder must tell Alpha to call store — that's the whole point."""
        assert "store tool" in REFLECTION_REMINDER

    def test_gives_explicit_null_case_permission(self):
        """The reminder must say finding nothing is a legitimate outcome.
        Without this, the model manufactures significance where none exists."""
        assert "correct outcome" in REFLECTION_REMINDER

    def test_reminds_conversation_is_still_waiting(self):
        """The reminder must not be mistaken for Jeffery's reply — the
        conversation is still waiting on his actual next message."""
        assert "still waiting" in REFLECTION_REMINDER

    def test_identifies_source_as_alpha_app_not_jeffery(self):
        """Voice channel separation: the reminder must name itself as
        system voice so Alpha doesn't treat it as user input."""
        assert "not from Jeffery" in REFLECTION_REMINDER

    def test_anti_fourth_wall_clause(self):
        """Alpha must not reference the reminder in her reply to Jeffery."""
        assert "Do not reference this reminder" in REFLECTION_REMINDER

    def test_contains_no_prohibition_on_generating_text(self):
        """Explicitly verify the silence-shaped-void antipattern is absent.
        Phrases like 'do not respond' create a silence the model must fill,
        which is the failure mode this redesign is correcting."""
        lowered = REFLECTION_REMINDER.lower()
        assert "do not respond" not in lowered
        assert "do not acknowledge" not in lowered
        assert "do not produce any text" not in lowered
        assert "produce no output" not in lowered


class TestBuildReflectionReminder:
    def test_no_flags_returns_base_reminder(self):
        """With no flags, the builder should return the base reminder unchanged."""
        assert build_reflection_reminder(None) == REFLECTION_REMINDER
        assert build_reflection_reminder([]) == REFLECTION_REMINDER

    def test_single_flag_prepends_note(self):
        """A single flag should surface as a bullet at the top of the reminder."""
        result = build_reflection_reminder(["Jeffery mentioned he loved the not-knowing part"])
        assert "Jeffery mentioned he loved the not-knowing part" in result
        assert "a note" in result
        assert result.endswith(REFLECTION_REMINDER)

    def test_multiple_flags_count_and_bullets(self):
        """Multiple flags should be listed as bullets with a count."""
        notes = ["First moment", "Second moment", "Third moment"]
        result = build_reflection_reminder(notes)
        assert "3 notes" in result
        for n in notes:
            assert n in result
        # Each flag rendered as a bullet.
        assert result.count("  • ") == 3

    def test_flag_block_is_its_own_system_reminder(self):
        """The flag block must be wrapped in its own <system-reminder> tags
        so voice separation holds — the flags are system context, not Jeffery."""
        result = build_reflection_reminder(["a bookmark"])
        assert result.startswith("<system-reminder>")
        # Two system-reminder blocks: the flag block and the base reminder.
        assert result.count("<system-reminder>") == 2
        assert result.count("</system-reminder>") == 2
