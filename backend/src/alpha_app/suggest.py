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
