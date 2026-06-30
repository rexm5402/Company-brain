"""repos table and ticket repo_id column

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "repos",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("owner", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(200), nullable=False, unique=True),
        sa.Column("github_token_override", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_repos_slug", "repos", ["slug"])

    op.add_column("tickets", sa.Column("repo_id", sa.UUID(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "repo_id")
    op.drop_index("ix_repos_slug", table_name="repos")
    op.drop_table("repos")
