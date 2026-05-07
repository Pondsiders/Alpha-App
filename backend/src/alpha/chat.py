"""Chat — the in-memory domain object.

A `Chat` is one conversation: a row in `app.chats`, optionally an SDK
session, eventually a live `ClaudeSDKClient` and a reap timer. This
module only defines the shape and the ID generator; persistence lives
in `chats.py`, lifecycle lives wherever lifecycle ends up living.

`ChatId` is a constrained string — 21 characters from nanoid's default
URL-safe alphabet. Pydantic validates the shape at the wire boundary so
malformed IDs become a `validation-failed` event rather than a silent
"not found".
"""

from typing import Annotated

import nanoid
import pendulum
from pydantic import StringConstraints

ChatId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{21}$")]
"""A 21-character nanoid using the default URL-safe alphabet."""


def new_chat_id() -> ChatId:
    """Generate a fresh chat ID."""
    return nanoid.generate()


class Chat:
    """One chat — created by Dawn, continued through the day, forked by Dusk."""

    chat_id: ChatId
    session_id: str | None
    created_at: pendulum.DateTime
    last_active: pendulum.DateTime
    archived: bool
