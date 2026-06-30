"""Watchdog pipeline tables: users, webhook_events, tickets.source

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users ---------------------------------------------------------
    # Lightweight identity table: populated by OAuth (later) or seeded
    # manually. The watchdog uses github_username to assign tickets.
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("github_username", sa.String(160), nullable=False, unique=True),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("slack_user_id", sa.String(80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_users_github_username", "users", ["github_username"])

    # --- webhook_events ------------------------------------------------
    # Audit log for all inbound signals (Sentry, GitHub CI). Lets us dedupe
    # replays (check external_id before creating a duplicate ticket) and
    # diagnose missed or double-processed events.
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "source",
            sa.String(40),
            nullable=False,
            comment="sentry | github_ci",
        ),
        sa.Column("event_type", sa.String(80), nullable=False),
        # Sentry issue ID or GitHub check_suite/workflow_run node_id
        sa.Column("external_id", sa.String(200), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=True),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_webhook_events_external_id", "webhook_events", ["external_id"])
    op.create_index("ix_webhook_events_source", "webhook_events", ["source"])

    # --- tickets.source ------------------------------------------------
    # Track where each ticket came from: human created it, or the watchdog.
    op.add_column(
        "tickets",
        sa.Column(
            "source",
            sa.String(40),
            nullable=False,
            server_default="manual",
        ),
    )


def downgrade() -> None:
    op.drop_column("tickets", "source")
    op.drop_index("ix_webhook_events_source", table_name="webhook_events")
    op.drop_index("ix_webhook_events_external_id", table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_index("ix_users_github_username", table_name="users")
    op.drop_table("users")
