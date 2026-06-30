"""deployments table

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("pr_url", sa.Text(), nullable=True),
        sa.Column("deploy_url", sa.Text(), nullable=True),
        sa.Column("branch", sa.String(200), nullable=True),
        sa.Column("repo", sa.String(200), nullable=True),
        sa.Column(
            "status",
            sa.String(40),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_deployments_ticket_id", "deployments", ["ticket_id"])


def downgrade() -> None:
    op.drop_index("ix_deployments_ticket_id", table_name="deployments")
    op.drop_table("deployments")
