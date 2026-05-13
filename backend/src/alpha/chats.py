"""Persistence for `app.chats` — module-level functions over the live pool.

Each function acquires a connection from `db.get()`, runs one statement,
and releases. No class wrapper, no broadcasting; this module returns and
persists data. Telling other parts of the system that something changed
is the caller's job.
"""

from datetime import datetime

import asyncpg
import pendulum

from alpha import db
from alpha.chat import Chat, new_chat_id
from alpha.ws.events import ChatSummary


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


async def all(*, include_archived: bool = False) -> list[Chat]:
    """Return chats for the sidebar, newest first.

    By default, archived chats are excluded; pass `include_archived=True`
    to get the full list (e.g. for a long-term archive view).
    """
    if include_archived:
        sql = """
            SELECT chat_id, session_id, created_at, last_active, archived
            FROM app.chats
            ORDER BY created_at DESC
        """
    else:
        sql = """
            SELECT chat_id, session_id, created_at, last_active, archived
            FROM app.chats
            WHERE archived = FALSE
            ORDER BY created_at DESC
        """
    async with db.get().acquire() as conn:
        rows = await conn.fetch(sql)
    return [_hydrate(row) for row in rows]


def summary_of(chat: Chat) -> ChatSummary:
    """Project a Chat into the ChatSummary the wire carries."""
    return ChatSummary(
        chat_id=chat.chat_id,
        created_at=chat.created_at,
        last_active=chat.last_active,
        state="pending",
        token_count=0,
        context_window=1_000_000,
    )


def _hydrate(row: asyncpg.Record) -> Chat:
    """Build a Chat from one asyncpg Record. Narrows column types at the seam.

    asyncpg returns Record objects whose column accessors are loosely typed.
    The asserts narrow each column to the type Chat expects; a failure means
    the table schema and the Python types have drifted.
    """
    chat_id = row["chat_id"]
    session_id = row["session_id"]
    created_at = row["created_at"]
    last_active = row["last_active"]
    archived = row["archived"]
    assert isinstance(chat_id, str)  # noqa: S101 — type narrowing at the asyncpg→Chat seam
    assert session_id is None or isinstance(session_id, str)  # noqa: S101
    assert isinstance(created_at, datetime)  # noqa: S101
    assert isinstance(last_active, datetime)  # noqa: S101
    assert isinstance(archived, bool)  # noqa: S101
    return Chat(
        chat_id=chat_id,
        session_id=session_id,
        created_at=pendulum.instance(created_at),
        last_active=pendulum.instance(last_active),
        archived=archived,
    )
