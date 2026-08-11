import uuid
from datetime import date as _Date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

AlertType = Literal["7_DAYS", "3_DAYS", "1_DAY", "DUE_DATE"]
NotificationStatus = Literal["unread", "read", "dismissed"]


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    account_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    alert_type: AlertType
    status: NotificationStatus
    description: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    type: Optional[str] = None
    account_name: Optional[str] = None
    due_date: _Date
    created_at: datetime
    sent_at: Optional[datetime] = None


class UnreadCount(BaseModel):
    count: int
