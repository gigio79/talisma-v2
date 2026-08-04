"""add due_date and scheduled status for "a pagar" transactions

Revision ID: 067
Revises: 066
Create Date: 2026-08-01

Adds a nullable `due_date` column and backfills the new `scheduled`
status onto manual/recurring debit transactions that are dated in the
future. A `scheduled` transaction does NOT count toward the account
balance until the user marks it as paid (`posted`), which is what makes
the wallet balance match the real bank account.

Backfill rule: source in (manual, recurring), type = debit,
status = posted, date > today -> status = scheduled, due_date = date.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "067"
down_revision: Union[str, None] = "066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("due_date", sa.Date(), nullable=True))

    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE transactions
            SET status = 'scheduled', due_date = date
            WHERE source IN ('manual', 'recurring')
              AND type = 'debit'
              AND status = 'posted'
              AND date > CURRENT_DATE
            """
        )
    )


def downgrade() -> None:
    # Restore future-dated scheduled transactions to their prior posted state.
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE transactions
            SET status = 'posted'
            WHERE status = 'scheduled'
              AND source IN ('manual', 'recurring')
              AND type = 'debit'
            """
        )
    )
    op.drop_column("transactions", "due_date")
