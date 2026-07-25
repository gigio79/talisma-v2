
from pydantic import BaseModel, Field


class MacroDroidPayload(BaseModel):
    """Payload received from MacroDroid webhook.

    - text: raw notification text
    - sender: who triggered the notification (optional)
    - app: which app generated the notification (optional)
    """

    text: str = Field(..., min_length=1, description="Raw notification text from MacroDroid")
    sender: str | None = Field(None, description="Person/device that received the notification")
    app: str | None = Field(None, description="App name that generated the notification (PicPay, Neon, etc.)")
