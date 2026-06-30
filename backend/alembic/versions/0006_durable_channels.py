"""durable chat channels + consensus state

Moves the local-test chat and per-ticket discussion channels out of process
memory and into Postgres, so messages survive restarts and the consensus
"claim" can be a row-level compare-and-swap.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channel_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("channel", sa.String(length=160), nullable=False),
        sa.Column("author", sa.String(length=160), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_bot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pr_url", sa.Text(), nullable=True),
        sa.Column("ts", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_channel_messages_channel", "channel_messages", ["channel"]
    )
    op.create_index("ix_channel_messages_ts", "channel_messages", ["ts"])

    op.create_table(
        "channel_state",
        sa.Column("channel", sa.String(length=160), primary_key=True),
        sa.Column("consumed_ts", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "draft_consumed_ts", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column(
            "questions_asked", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_table("channel_state")
    op.drop_index("ix_channel_messages_ts", table_name="channel_messages")
    op.drop_index("ix_channel_messages_channel", table_name="channel_messages")
    op.drop_table("channel_messages")
