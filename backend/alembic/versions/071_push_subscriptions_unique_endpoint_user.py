"""make push endpoint unique per user

Push endpoints were globally unique, so a second user registering from the
same browser could hijack the first user's subscription row (the endpoint is
per-origin). Scope the uniqueness to (endpoint, user_id) so every workspace
member keeps their own push subscription even when sharing a device.

Revision ID: 071
Revises: 070
Create Date: 2026-08-07
"""
from typing import Sequence, Union

from alembic import op

revision: str = "071"
down_revision: Union[str, None] = "070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_push_subscriptions_endpoint", "push_subscriptions", type_="unique")
    op.create_unique_constraint(
        "uq_push_subscriptions_endpoint_user",
        "push_subscriptions",
        ["endpoint", "user_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_push_subscriptions_endpoint_user", "push_subscriptions", type_="unique"
    )
    op.create_unique_constraint(
        "uq_push_subscriptions_endpoint", "push_subscriptions", ["endpoint"]
    )
