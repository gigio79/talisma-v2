"""transaction reconciliation link

When a real payment (imported/synced/MacroDroid) is reconciled with a
scheduled ("a pagar") transaction, the scheduled row survives and absorbs
the real one, which is deleted. `reconciled_with_id` records the audit link
("this row absorbed tx X") and lets the smart-match pass skip pairs that
have already been merged.

Revision ID: 072
Revises: 071
Create Date: 2026-08-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "072"
down_revision: Union[str, None] = "071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("reconciled_with_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_transactions_reconciled_with_id", "transactions", ["reconciled_with_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_reconciled_with_id", table_name="transactions")
    op.drop_column("transactions", "reconciled_with_id")
