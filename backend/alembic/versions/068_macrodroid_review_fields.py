"""add card_last4, needs_review, movement_type to transactions

Revision ID: 068
Revises: 067
Create Date: 2026-08-03

Adds three columns backing the enhanced MacroDroid notification parser:
- card_last4: last 4 digits of the card used (cartão final)
- needs_review: flagged for manual review when parsing was incomplete
- movement_type: granular movement (pix_recebido/pix_enviado/debito/credito)

`type` (debit/credit) remains derived from movement_type at write time.
All columns are nullable/optional so existing rows are unaffected.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "068"
down_revision: Union[str, None] = "067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("card_last4", sa.String(4), nullable=True))
    op.add_column(
        "transactions",
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("transactions", sa.Column("movement_type", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "movement_type")
    op.drop_column("transactions", "needs_review")
    op.drop_column("transactions", "card_last4")
