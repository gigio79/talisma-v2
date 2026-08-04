"""Webhook endpoint for receiving MacroDroid notifications.

MacroDroid sends notification text from banking apps (PicPay, Neon, Nubank, etc.)
which is parsed into structured transactions. Authentication is via a shared API
key in the Authorization header (Bearer <key>).

Fallback rules (from the MacroDroid spec):
- No value extracted → transaction is NOT created; a log entry + admin alert
  is emitted instead.
- Value found but no structured match → a minimal transaction flagged
  ``needs_review`` is created ("Transação pendente de revisão - {app}").
- Value found but no establishment → "Estabelecimento não identificado"
  placeholder and ``needs_review`` is flagged.
"""
import logging
import unicodedata
import uuid
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_async_session
from app.models.account import Account
from app.models.category import Category
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.parsing import parse_notification
from app.parsing.base import (
    CREDITO,
    DEBITO,
    PIX_ENVIADO,
    PIX_RECEBIDO,
    ParsedTransaction,
    build_description,
)
from app.schemas.webhook import MacroDroidPayload
from app.services.payee_service import get_or_create_payee

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

NOTES_FIXO = "Criado automaticamente pelo MacroDroid:"

# Placeholder payees that should not become a transaction's `payee`.
_PAYEE_PLACEHOLDERS = {"Não informado", "Estabelecimento não identificado"}

# Granular movement type → account type the transaction must land on.
_TIPO_CONTA_POR_MOVIMENTO = {
    PIX_RECEBIDO: "checking",
    PIX_ENVIADO: "checking",
    DEBITO: "checking",
    CREDITO: "credit_card",
}


def _alert_admin(message: str) -> None:
    """Emit a structured, greppable admin alert (push is out of scope for now)."""
    logger.error("ALERTA_ADMIN | %s", message)


def _normalize_name(value: str) -> str:
    """Lowercase and strip accents for loose name matching."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return value.strip().lower()


async def _resolve_sender_user(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    sender: str | None,
) -> User | None:
    """Resolve the workspace member behind a MacroDroid sender name.

    Matches ``sender`` (e.g. "Giovanni", "Débora") against each workspace
    member's ``preferences["display_name"]`` or the local part of their
    email, ignoring case and accents. Returns ``None`` when no member
    matches — the caller then falls back to the workspace owner.
    """
    if not sender:
        return None
    result = await session.execute(
        select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == workspace_id)
    )
    user_ids = [row[0] for row in result]
    if not user_ids:
        return None
    result = await session.execute(select(User).where(User.id.in_(user_ids)))
    wanted = _normalize_name(sender)
    for user in result.scalars():
        prefs = user.preferences or {}
        candidates = [
            prefs.get("display_name") or "",
            (user.email or "").split("@", 1)[0],
        ]
        if any(c and _normalize_name(c) == wanted for c in candidates):
            return user
    return None


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
    movement_type: str = "",
    card_last4: str | None = None,
    user_id: uuid.UUID | None = None,
) -> Account:
    """Resolve the account a MacroDroid transaction should land on.

    Routing rule (approved by the user):
    - Pix recebido/enviado e compra no débito → conta corrente (checking) do banco.
    - Compra no crédito → conta de cartão de crédito do banco, preferindo a conta
      cujo ``masked_number`` bate com ``card_last4``.
    - Se não existir a conta específica → conta padrão do app (primeira encontrada
      pelo nome, qualquer tipo).
    - Se não existir nenhuma → cria uma conta corrente (checking) para o app.

    When ``user_id`` is given (the sender's resolved titular), every lookup is
    scoped to that user's own accounts first, falling back to the workspace-wide
    rules above when the user has no matching account.
    """
    tipo_conta = _TIPO_CONTA_POR_MOVIMENTO.get(movement_type, "checking")

    # 1) Specific credit-card account: match the card's last 4 digits first.
    if movement_type == CREDITO and card_last4:
        conditions = [
            Account.workspace_id == workspace_id,
            Account.type == "credit_card",
            Account.masked_number == card_last4,
        ]
        if user_id:
            conditions.append(Account.user_id == user_id)
        result = await session.execute(
            select(Account).where(*conditions).order_by(Account.name).limit(1)
        )
        account = result.scalar_one_or_none()
        if account:
            return account

    # 2) Specific account for the movement type (checking / credit_card).
    conditions = [
        Account.workspace_id == workspace_id,
        Account.type == tipo_conta,
        Account.name.ilike(banco_app),
    ]
    if user_id:
        conditions.append(Account.user_id == user_id)
    result = await session.execute(
        select(Account).where(*conditions).order_by(Account.name).limit(1)
    )
    account = result.scalar_one_or_none()
    if account:
        return account

    # 3) Fallback: default account of the app (first found by name, any type).
    conditions = [
        Account.workspace_id == workspace_id,
        Account.name.ilike(banco_app),
    ]
    if user_id:
        conditions.append(Account.user_id == user_id)
    result = await session.execute(
        select(Account).where(*conditions).order_by(Account.name).limit(1)
    )
    account = result.scalar_one_or_none()
    if account:
        return account

    # 4) No account exists — create one for the app with the movement's type.
    account = Account(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        user_id=uuid.uuid4(),  # placeholder — will be overridden below
        name=banco_app,
        type=tipo_conta,
        currency="BRL",
        balance=Decimal("0.00"),
    )
    return account


def _apply_estabelecimento_override(
    parsed: ParsedTransaction,
    estabelecimento: str | None,
) -> ParsedTransaction:
    """Override the parsed beneficiary/establishment with the user's value.

    When the user fills ``estabelecimento`` in the payload it takes priority
    over the name extracted by the parser, both for the transaction's
    ``payee`` and for the description. When a structured movement exists the
    description is rebuilt without the amount and the transaction no longer
    needs review (the "estabelecimento não identificado" placeholder is gone).
    """
    if not estabelecimento or not estabelecimento.strip():
        return parsed
    estab = estabelecimento.strip()
    if parsed.movement_type:
        return replace(
            parsed,
            origem_destino=estab,
            descricao=build_description(parsed.movement_type, estab, parsed.banco_app),
            precisa_revisao=False,
        )
    # Unstructured fallback (no movement type) — keep review but use the
    # user's establishment as the beneficiary.
    return replace(parsed, origem_destino=estab)


async def _resolve_category(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    categoria: str | None,
) -> tuple[Category | None, str | None]:
    """Resolve (or auto-create) a category by name.

    Returns ``(category, category_name)``. When the payload has no category
    both are ``None``. The lookup is case-insensitive; if no match exists the
    category is created in the workspace with no group (group_id=null).
    """
    if not categoria or not categoria.strip():
        return None, None
    name = categoria.strip()

    result = await session.execute(
        select(Category).where(
            Category.workspace_id == workspace_id,
            func.lower(Category.name) == name.lower(),
        )
    )
    category = result.scalar_one_or_none()
    if category:
        return category, category.name

    category = Category(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        name=name,
        icon="circle-help",
        color="#6B7280",
        is_system=False,
        group_id=None,
    )
    session.add(category)
    await session.flush()
    return category, category.name


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
    logger.info(
        "MacroDroid notification received | app=%s sender=%s",
        payload.app,
        payload.sender,
    )

    # Parse the notification
    parsed = parse_notification(payload.text, app=payload.app or "", sender=payload.sender or "")
    if parsed is None:
        _alert_admin(
            f"Falha ao parsear notificação sem valor | app={payload.app} "
            f"sender={payload.sender} | texto={payload.text[:200]!r}"
        )
        logger.warning(
            "Notificação sem valor extraído — transação NÃO criada | app=%s sender=%s",
            payload.app,
            payload.sender,
        )
        raise HTTPException(
            status_code=422,
            detail="Could not parse notification text (no value found)",
        )

    # User-filled establishment takes priority over the parser-extracted name.
    parsed = _apply_estabelecimento_override(parsed, payload.estabelecimento)

    # Resolve workspace: payload > env var > first non-archived
    settings = get_settings()
    workspace = None

    if payload.workspace_id:
        try:
            ws_id = uuid.UUID(payload.workspace_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid workspace_id format")
        result = await session.execute(
            select(Workspace).where(Workspace.id == ws_id)
        )
        workspace = result.scalar_one_or_none()
        if not workspace:
            raise HTTPException(status_code=404, detail=f"Workspace {payload.workspace_id} not found")

    if not workspace and settings.macrodroid_workspace_id:
        try:
            ws_id = uuid.UUID(settings.macrodroid_workspace_id)
        except ValueError:
            raise HTTPException(status_code=500, detail="Invalid MACRODROID_WORKSPACE_ID in config")
        result = await session.execute(
            select(Workspace).where(Workspace.id == ws_id)
        )
        workspace = result.scalar_one_or_none()

    if not workspace:
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

    if parsed.precisa_revisao:
        logger.warning(
            "Transação marcada para revisão | app=%s sender=%s descricao=%r valor=%s",
            payload.app,
            payload.sender,
            parsed.descricao,
            parsed.valor,
        )

    # Resolve the titular behind the sender (e.g. "Giovanni" → his own account).
    # Falls back to the workspace owner when no member matches the sender.
    sender_user = await _resolve_sender_user(session, workspace.id, payload.sender)
    account_user_id = sender_user.id if sender_user else owner_id

    # Resolve (or auto-create) the user-filled category.
    category, category_name = await _resolve_category(
        session, workspace.id, owner_id, payload.categoria
    )

    # Resolve account — find or create, preferring the sender's own account.
    account = await _resolve_account(
        session,
        workspace.id,
        parsed.banco_app,
        movement_type=parsed.movement_type or "",
        card_last4=parsed.cartao_final,
        user_id=account_user_id,
    )
    if not account.id:
        account.id = uuid.uuid4()
    account.user_id = account_user_id
    session.add(account)
    await session.flush()

    # Description: append the sender's name when the transaction needs review,
    # e.g. "Transação pendente de revisão - PicPay (Giovanni)".
    descricao = parsed.descricao
    if parsed.precisa_revisao and payload.sender:
        descricao = f"{descricao} ({payload.sender})"

    # Notes: translated prefix + full local timestamp so the hour is visible.
    notes = f"{NOTES_FIXO} {datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y %H:%M:%S')}"

    # Payee: user-filled establishment or the parser-extracted name. Real names
    # become a Payee entity so they show up in the merchants list; placeholders
    # stay unset.
    payee = (
        parsed.origem_destino if parsed.origem_destino not in _PAYEE_PLACEHOLDERS else None
    )
    payee_id = None
    if payee:
        payee_entity = await get_or_create_payee(
            session, owner_id, payee, workspace_id=workspace.id
        )
        payee_id = payee_entity.id

    # Create the transaction
    from app.models.transaction import Transaction
    from app.services.credit_card_service import apply_effective_date

    transaction = Transaction(
        user_id=owner_id,
        workspace_id=workspace.id,
        account_id=account.id,
        category_id=category.id if category else None,
        description=descricao,
        amount=parsed.valor,
        currency="BRL",
        date=date.today(),
        type=parsed.tipo,
        source="manual",
        status="posted",
        payee=payee,
        payee_id=payee_id,
        notes=notes,
        sender=payload.sender,
        source_app=parsed.banco_app,
        movement_type=parsed.movement_type or None,
        card_last4=parsed.cartao_final,
        needs_review=parsed.precisa_revisao,
        raw_data={
            "origem_destino": parsed.origem_destino,
            "movement_type": parsed.movement_type or None,
            "cartao_final": parsed.cartao_final,
            "needs_review": parsed.precisa_revisao,
            "estabelecimento": payload.estabelecimento,
            "categoria": payload.categoria,
            "notificacao_original": payload.text,
        },
    )
    apply_effective_date(transaction, account)
    session.add(transaction)
    await session.commit()
    await session.refresh(transaction)

    logger.info(
        "MacroDroid transaction created | id=%s app=%s tipo=%s valor=%s needs_review=%s "
        "categoria=%s",
        transaction.id,
        parsed.banco_app,
        parsed.movement_type,
        transaction.amount,
        transaction.needs_review,
        category_name,
    )

    return {
        "status": "ok",
        "transaction_id": str(transaction.id),
        "description": transaction.description,
        "amount": str(transaction.amount),
        "type": transaction.type,
        "movement_type": transaction.movement_type,
        "needs_review": transaction.needs_review,
        "account": account.name,
        "payee": payee,
        "category": category_name,
    }
