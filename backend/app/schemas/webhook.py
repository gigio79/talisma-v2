
from pydantic import BaseModel, Field


class MacroDroidPayload(BaseModel):
    """Payload received from MacroDroid webhook.

    - text: raw notification text
    - sender: who triggered the notification (optional)
    - app: which app generated the notification (optional)
    - workspace_id: target workspace UUID (optional, overrides MACRODROID_WORKSPACE_ID env var)
    """

    text: str = Field(..., min_length=1, description="Raw notification text from MacroDroid")
    sender: str | None = Field(None, description="Person/device that received the notification")
    app: str | None = Field(None, description="App name that generated the notification (PicPay, Neon, etc.)")
    workspace_id: str | None = Field(None, description="Target workspace UUID (optional)")
    estabelecimento: str | None = Field(
        None,
        description="Establishment/beneficiary name manually filled by the user. "
        "When present, it overrides the name extracted by the parser.",
    )
    categoria: str | None = Field(
        None,
        description="Category name manually filled by the user. When present, the "
        "transaction is categorized with it (created on the fly if it doesn't exist).",
    )
