"""create notifications table

Due-date alert system (issue: vencimento de contas).

Each row is one alert fired as a bill ("conta a pagar") or credit-card
invoice approaches its due date. Carries a display snapshot so reads never
join against entities that may be edited/deleted later.

Deduplication: one alert per (user, target, alert_type, due date cycle) via
`uq_notifications_target_alert_cycle` — the hourly job is idempotent.

Revision ID: 069
Revises: 068
Create Date: 2026-08-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "069"
down_revision: Union[str, None] = "068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alert_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="unread"),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("type", sa.String(length=10), nullable=True),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "target_type", "target_id", "alert_type", "due_date",
            name="uq_notifications_target_alert_cycle",
        ),
    )
    op.create_index("ix_notifications_workspace_id", "notifications", ["workspace_id"])
    op.create_index("ix_notifications_account_id", "notifications", ["account_id"])
    op.create_index("ix_notifications_due_date", "notifications", ["due_date"])
    op.create_index(
        "ix_notifications_user_id_status", "notifications", ["user_id", "status"]
    )
    op.create_index("ix_notifications_user_id_due_date", "notifications", ["user_id", "due_date"])


def downgrade() -> None:
    op.drop_index("ix_notifications_user_id_due_date", table_name="notifications")
    op.drop_index("ix_notifications_user_id_status", table_name="notifications")
    op.drop_index("ix_notifications_due_date", table_name="notifications")
    op.drop_index("ix_notifications_account_id", table_name="notifications")
    op.drop_index("ix_notifications_workspace_id", table_name="notifications")
    op.drop_table("notifications")
