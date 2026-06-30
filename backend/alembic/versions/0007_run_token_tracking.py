"""add token usage + cost columns to runs

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("prompt_tokens", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("completion_tokens", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("cost_usd", sa.Float(), nullable=True))
    op.add_column("runs", sa.Column("model", sa.String(length=64), nullable=True))
    op.add_column("runs", sa.Column("error_detail", sa.Text(), nullable=True))


def downgrade() -> None:
    for col in ("error_detail", "model", "cost_usd", "completion_tokens", "prompt_tokens"):
        op.drop_column("runs", col)
