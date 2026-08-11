import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PushSubscription(Base):
    """A browser/device subscribed to Web Push (VAPID) notifications.

    One row per (user, PushManager subscription): the browser endpoint URL plus
    the encryption keys needed to deliver a payload. Endpoints that stop
    working (404/410 from the push service) are pruned on send — but only once
    they have aged out of the fresh window.
    """

    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The browser's push service endpoint (https://fcm.googleapis.com/...,
    # https://push.apple.com/... etc). Unique per user, so re-subscribing is a
    # no-op and two members sharing a browser keep separate rows.
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(200), nullable=False)
    # Free-form device label (e.g. "Pixel 8 · Chrome").
    device_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint("endpoint", "user_id", name="uq_push_subscriptions_endpoint_user"),
    )
