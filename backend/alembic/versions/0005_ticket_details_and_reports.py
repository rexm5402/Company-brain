"""add ticket details column and reports table

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # #2 enrichment: AI-expanded summary / acceptance criteria, as markdown.
    op.add_column("tickets", sa.Column("details", sa.Text(), nullable=True))

    # #7 final report (minutes-of-meeting) stored per ticket.
    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticket_key", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),  # rendered markdown
        sa.Column("data", sa.Text(), nullable=True),  # JSON blob of structured fields
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("reports")
    op.drop_column("tickets", "details")
