"""add sender and source_app to transactions for MacroDroid webhook

Revision ID: 066
Revises: 065
Create Date: 2026-07-23

Adds two nullable columns to store MacroDroid webhook metadata:
- sender: who triggered the notification (e.g. "Giovanni", "Aline")
- source_app: which app generated the notification (e.g. "PicPay", "Neon")

Both are nullable so existing rows and non-webhook transactions are unaffected.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "066"
down_revision: Union[str, None] = "065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("sender", sa.String(255), nullable=True))
    op.add_column("transactions", sa.Column("source_app", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "source_app")
    op.drop_column("transactions", "sender")
