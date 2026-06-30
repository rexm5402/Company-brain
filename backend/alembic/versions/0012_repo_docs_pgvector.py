"""repo_docs table with pgvector embedding

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "repo_docs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("repo_id", sa.UUID(), nullable=False),
        sa.Column("path", sa.String(500), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_repo_docs_repo_path_chunk",
        "repo_docs",
        ["repo_id", "path", "chunk_index"],
    )
    # Add the vector column separately (requires pgvector extension)
    op.execute("ALTER TABLE repo_docs ADD COLUMN embedding vector(1536)")


def downgrade() -> None:
    op.drop_table("repo_docs")
