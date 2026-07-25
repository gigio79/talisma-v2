"""Webhook endpoint for receiving MacroDroid notifications.

MacroDroid sends notification text from banking apps (PicPay, Neon, Nubank, etc.)
which is parsed into structured transactions. Authentication is via a shared API
key in the Authorization header (Bearer <key>).
"""
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_async_session
from app.models.account import Account
from app.models.workspace import Workspace
from app.parsing import parse_notification
from app.schemas.webhook import MacroDroidPayload

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


async def _verify_webhook_auth(
    authorization: str | None = Header(None),
) -> None:
    """Verify Bearer token matches the configured webhook secret."""
    settings = get_settings()
    if not settings.macrodroid_webhook_secret:
        return  # auth disabled
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != settings.macrodroid_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook credentials")


async def _resolve_account(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    banco_app: str,
) -> Account:
    """Find or create a checking account for the given bank/app name.

    Looks for an existing account by name (case-insensitive) in the workspace.
    If none exists, creates a new checking account with BRL currency.
    """
    result = await session.execute(
        select(Account).where(
            Account.workspace_id == workspace_id,
            Account.name.ilike(banco_app),
        )
    )
    account = result.scalar_one_or_none()
    if account:
        return account

    # Create a new account for this bank/app
    account = Account(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        user_id=uuid.uuid4(),  # placeholder — will be overridden below
        name=banco_app,
        type="checking",
        currency="BRL",
        balance=Decimal("0.00"),
    )
    return account


@router.post("/macrodroid")
async def receive_macrodroid_notification(
    payload: MacroDroidPayload,
    _: None = Depends(_verify_webhook_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Receive a notification from MacroDroid and create a transaction.

    Expected payload from MacroDroid:
    ```json
    {
        "text": "notification text",
        "sender": "Giovanni",
        "app": "PicPay"
    }
    ```
    """
    # Parse the notification
    parsed = parse_notification(payload.text, app=payload.app or "", sender=payload.sender or "")
    if parsed is None:
        raise HTTPException(
            status_code=422,
            detail=f"Could not parse notification text: {payload.text[:200]}",
        )

    # Get the default workspace (first non-archived)
    result = await session.execute(
        select(Workspace).where(Workspace.is_archived == False).limit(1)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="No workspace found")

    # Get the workspace owner (created_by_user_id)
    owner_id = workspace.created_by_user_id
    if not owner_id:
        raise HTTPException(status_code=500, detail="Workspace has no owner")

    # Resolve account — find or create
    account = await _resolve_account(session, workspace.id, parsed.banco_app)
    if not account.id:
        account.id = uuid.uuid4()
    account.user_id = owner_id
    session.add(account)
    await session.flush()

    # Create the transaction
    from app.models.transaction import Transaction
    from app.services.credit_card_service import apply_effective_date

    transaction = Transaction(
        user_id=owner_id,
        workspace_id=workspace.id,
        account_id=account.id,
        description=parsed.descricao,
        amount=parsed.valor,
        currency="BRL",
        date=date.today(),
        type=parsed.tipo,
        source="manual",
        status="posted",
        payee=parsed.origem_destino if parsed.origem_destino != "Pix enviado" else None,
        notes="Auto-created from MacroDroid:",
        sender=payload.sender,
        source_app=parsed.banco_app,
    )
    apply_effective_date(transaction, account)
    session.add(transaction)
    await session.commit()
    await session.refresh(transaction)

    return {
        "status": "ok",
        "transaction_id": str(transaction.id),
        "description": transaction.description,
        "amount": str(transaction.amount),
        "type": transaction.type,
        "account": account.name,
    }
