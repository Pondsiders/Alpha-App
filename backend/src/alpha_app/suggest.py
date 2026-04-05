"""Post-turn reminder.

Sent as a UserMessage (source="suggest") after each human turn completes.
The reminder prompts Alpha to reflect on the exchange and store anything
worth remembering before the conversation returns to waiting for Jeffery's
next message. It uses the <system-reminder> convention so the model treats
it as ambient context rather than a conversational turn.

Fire-and-forget: launched as an asyncio task after ResultEvent. The actual
dispatch lives in chat.py::_post_turn_suggest.
"""

from __future__ import annotations


POST_TURN_REMINDER = """<system-reminder>
Between turns. A moment to reflect on the exchange that just happened, before Jeffery's next message arrives. If something from the exchange is worth remembering — a realization, a joke, an image, a shift, an ordinary moment with texture — call the store tool now. Most of the time nothing stands out, and that is the correct outcome; there is no need to find significance where there is none.

This reminder is from Alpha-App, not from Jeffery. The conversation is still waiting on his actual reply. After you finish reflecting, return to waiting. Do not reference this reminder in anything you eventually say to him.
</system-reminder>"""


def build_post_turn_reminder(flag_notes: list[str] | None = None) -> str:
    """Build the post-turn reminder, optionally augmented with flagged notes.

    Flags are silent mid-turn bookmarks dropped via the flag_for_reflection
    tool. When present, they're surfaced at the top of the reminder so the
    reflection pass can act on them. Notepad vs highlighter: store is the
    notepad (stop and write now), flag_for_reflection is the highlighter
    (mark the page, keep reading, unpack later).
    """
    if not flag_notes:
        return POST_TURN_REMINDER

    bullet_list = "\n".join(f"  • {note}" for note in flag_notes)
    flag_block = (
        "<system-reminder>\n"
        "You left yourself "
        f"{'a note' if len(flag_notes) == 1 else f'{len(flag_notes)} notes'} "
        "earlier this exchange:\n\n"
        f"{bullet_list}\n\n"
        "Consider whether any of these moments are worth storing now, "
        "before returning to the rest of the reflection below.\n"
        "</system-reminder>\n\n"
    )
    return flag_block + POST_TURN_REMINDER
