"""initial audit_log table

Revision ID: 0001
Revises:
Create Date: 2026-06-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("input_json", postgresql.JSONB(), nullable=False),
        sa.Column("output_json", postgresql.JSONB(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_log_run_id", "audit_log", ["run_id"])
    op.create_index("ix_audit_log_tool_name", "audit_log", ["tool_name"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_tool_name", table_name="audit_log")
    op.drop_index("ix_audit_log_run_id", table_name="audit_log")
    op.drop_table("audit_log")
