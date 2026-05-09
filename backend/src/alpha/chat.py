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
    """One chat — created by Dawn, continued through the day, forked by Dusk.

    State machine
    -------------

    A chat is always in exactly one of five states:

    - ``pending`` — no Claude subprocess. The chat exists but has been reaped or
      never spawned. The first send transparently wakes it.
    - ``ready`` — subprocess alive and idle, awaiting input.
    - ``preprocessing`` — backend has the message; recall, timestamping, and
      normalization are in flight. Claude has not received the message yet.
    - ``processing`` — Claude has the message and is generating.
    - ``postprocessing`` — post-turn work (reflection, etc.) is running.

    Transitions:

    - ``pending → preprocessing`` — first human send wakes the subprocess;
      preprocessing runs during the cold start.
    - ``pending → processing`` — first non-human-sourced send (Dawn injection,
      etc.) wakes the subprocess directly into Claude.
    - ``ready → preprocessing`` — human send to a warm chat.
    - ``ready → processing`` — non-human send to a warm chat.
    - ``preprocessing → processing`` — preprocessing finishes; Claude has the
      message.
    - ``processing → ready`` — turn finished, no postprocessing this turn.
    - ``processing → postprocessing`` — turn finished; postprocessing begins.
    - ``postprocessing → ready`` — postprocessing finished.
    - ``ready → pending`` — reap timer fires; subprocess goes away.

    A newly created chat lands in ``pending``. The first message — human or
    otherwise — drives the first transition.

    The composer-input rule is a function of ``state`` alone: input is accepted
    when ``state`` is ``pending``, ``ready``, or ``postprocessing``; locked when
    ``state`` is ``preprocessing`` or ``processing``. No other field needs to be
    consulted. The state machine carries the meaning — ``postprocessing``'s
    whole job is to be the named state in which the human can talk over a
    reflection turn.

    The state field itself is not yet implemented on this class; ``state`` will
    land alongside the lifecycle wiring (subprocess spawn, preprocessing
    pipeline, reap timer). This docstring is the doctrine the implementation
    will codify.
    """

    chat_id: ChatId
    session_id: str | None
    created_at: pendulum.DateTime
    last_active: pendulum.DateTime
    archived: bool

    def __init__(
        self,
        *,
        chat_id: ChatId,
        session_id: str | None,
        created_at: pendulum.DateTime,
        last_active: pendulum.DateTime,
        archived: bool,
    ) -> None:
        """Build a Chat from explicit fields. Placeholder until chats.py lands."""
        self.chat_id = chat_id
        self.session_id = session_id
        self.created_at = created_at
        self.last_active = last_active
        self.archived = archived
