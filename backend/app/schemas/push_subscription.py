"""Schemas for Web Push (VAPID) subscriptions."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PushSubscriptionCreate(BaseModel):
    endpoint: str = Field(min_length=1, max_length=500)
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=200)
    device_label: str | None = Field(default=None, max_length=100)


class PushSubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    endpoint: str
    device_label: str | None = None
    created_at: datetime


class PushVapidKey(BaseModel):
    enabled: bool
    public_key: str = ""


class PushResult(BaseModel):
    sent: int = 0
    pruned: int = 0
