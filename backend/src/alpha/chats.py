"""Persistence for `app.chats` — module-level functions over the live pool.

Each function acquires a connection from `db.get()`, runs one statement,
and releases. No class wrapper, no broadcasting; this module returns and
persists data. Telling other parts of the system that something changed
is the caller's job.

Today only `create()` lives here. `get()`, `list_active()`, and `save()`
land as their first callers do.
"""

import pendulum

from alpha import db
from alpha.chat import Chat, new_chat_id


async def create() -> Chat:
    """Insert a new row into `app.chats` and return the hydrated `Chat`."""
    chat = Chat(
        chat_id=new_chat_id(),
        session_id=None,
        created_at=pendulum.now("UTC"),
        last_active=pendulum.now("UTC"),
        archived=False,
    )
    async with db.get().acquire() as conn:
        _ = await conn.execute(
            """
            INSERT INTO app.chats
                (chat_id, session_id, created_at, last_active, archived)
            VALUES ($1, $2, $3, $4, $5)
            """,
            chat.chat_id,
            chat.session_id,
            chat.created_at,
            chat.last_active,
            chat.archived,
        )
    return chat
