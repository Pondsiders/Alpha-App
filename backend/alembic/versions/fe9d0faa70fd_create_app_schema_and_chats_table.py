"""create app schema and chats table

Revision ID: fe9d0faa70fd
Revises:
Create Date: 2026-05-07 19:31:03.691801

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fe9d0faa70fd"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the `app` schema and the `app.chats` table."""
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute("""
        CREATE TABLE app.chats (
            chat_id TEXT PRIMARY KEY,
            session_id TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            last_active TIMESTAMPTZ NOT NULL,
            archived BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)


def downgrade() -> None:
    """Drop the `app.chats` table and the `app` schema."""
    op.execute("DROP TABLE IF EXISTS app.chats")
    op.execute("DROP SCHEMA IF EXISTS app")
