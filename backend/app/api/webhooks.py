"""Webhook endpoint for receiving MacroDroid notifications.

MacroDroid sends notification text from banking apps (PicPay, Neon, Nubank, etc.)
which is parsed into structured transactions. Authentication is via HTTP Basic
Auth (``talisma`` + shared secret) or, for backwards compatibility, a Bearer
token in the Authorization header.

A ``descricao`` field built by MacroDroid (``PIX ENVIADO DE {APP} PARA {NOME}``)
is the authoritative display text; ``text`` is only used for the amount, and
``horario`` sets the transaction date. Duplicate notifications are suppressed
via a SHA-256 fingerprint of text + horario + descricao.

Fallback rules (from the MacroDroid spec):
- No value extracted → transaction is NOT created; a log entry + admin alert
  is emitted instead.
- Value found but no structured match → a minimal transaction flagged
  ``needs_review`` is created ("Transação pendente de revisão - {app}").
- Value found but no establishment → "Estabelecimento não identificado"
  placeholder and ``needs_review`` is flagged.
"""
import base64
import binascii
import hashlib
import logging
import re
import secrets
import unicodedata
import uuid
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, or_, select
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
    MOVEMENT_TO_TIPO,
    PIX_ENVIADO,
    PIX_RECEBIDO,
    ParsedTransaction,
    build_description,
)
from app.schemas.webhook import MacroDroidPayload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

NOTES_FIXO = "Criado automaticamente pelo MacroDroid:"

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


# MacroDroid-built description ("descricao"), which always arrives as
# "PIX ENVIADO DE {APP} PARA {NOME}" (app already uppercased by MacroDroid).
# Also accepts the "PIX RECEBIDO DE {APP} DE {NOME}" variant.
_DESCRICAO_PIX = re.compile(
    r"^\s*PIX\s+(ENVIADO|RECEBIDO)\s+DE\s+(.+?)\s+(?:PARA|DE)\s*(.*?)\s*$",
    re.IGNORECASE,
)

# Placeholder beneficiary when the MacroDroid description ends in "PARA"
# with no name (spec fallback).
DESTINATARIO_NAO_INFORMADO = "Destinatário não informado"


def _parse_descricao(descricao: str | None) -> tuple[str, str, str] | None:
    """Parse the MacroDroid ``descricao`` into ``(movement, app, beneficiary)``.

    Expected format: ``"PIX ENVIADO DE NEON PARA PADARIA"``. The beneficiary
    may be empty (``"PIX ENVIADO DE NEON PARA"``). Returns ``None`` when the
    string doesn't match the structured pattern — the caller then keeps the
    fields parsed from ``text``.
    """
    if not descricao or not descricao.strip():
        return None
    m = _DESCRICAO_PIX.match(descricao.strip())
    if not m:
        return None
    movement = PIX_ENVIADO if m.group(1).upper() == "ENVIADO" else PIX_RECEBIDO
    return movement, m.group(2).strip(), m.group(3).strip()


_HORARIO_FORMATS = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y")


def _parse_horario(horario: str | None) -> datetime | None:
    """Parse the MacroDroid ``horario`` into a datetime.

    Returns ``None`` when the field is absent. Raises ``ValueError`` when it
    is present but not parseable (the caller maps that to a 422).
    """
    if not horario or not horario.strip():
        return None
    for fmt in _HORARIO_FORMATS:
        try:
            return datetime.strptime(horario.strip(), fmt).replace(
                tzinfo=ZoneInfo("America/Sao_Paulo")
            )
        except ValueError:
            continue
    raise ValueError(f"Invalid horario format: {horario!r}")


def _dedup_key(text: str, horario: str | None, descricao: str | None) -> str:
    """Stable fingerprint of a notification for duplicate suppression.

    MacroDroid can capture the same notification more than once; identical
    (text, horario, descricao) → same hash → the second POST is ignored.
    """
    raw = "|".join([text.strip(), (horario or "").strip(), (descricao or "").strip()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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


def _basic_username(encoded: str) -> str | None:
    """Extract the username from a base64 Basic token for diagnostics.

    Returns ``None`` when the token is not valid base64 (logging only — the
    password is never logged)."""
    try:
        decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    username, _, _ = decoded.decode("utf-8", errors="replace").partition(":")
    return username


def _verify_basic_auth(encoded: str, settings) -> bool:
    """Validate a base64 Basic Auth token (``user:password``) using
    constant-time comparison against the configured secret.

    Only the password is compared: MacroDroid commonly sends an empty or
    arbitrary username (e.g. ``:password``), so the username is treated as a
    convention rather than a credential — the security lives in the long
    shared secret."""
    try:
        decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    _, _, password = decoded.decode("utf-8", errors="replace").partition(":")
    return secrets.compare_digest(password, settings.macrodroid_webhook_secret)


async def _verify_webhook_auth(
    authorization: str | None = Header(None),
) -> None:
    """Verify webhook credentials against the configured shared secret.

    Accepts HTTP Basic Auth (any username + the secret as password) and, for
    backwards compatibility, the legacy ``Bearer <key>`` form. Invalid
    credentials → 401. When no secret is configured the endpoint stays open
    (dev convenience). Failures are logged (scheme + username, never the
    password) so a misconfigured MacroDroid is easy to diagnose.
    """
    settings = get_settings()
    if not settings.macrodroid_webhook_secret:
        return  # auth disabled
    if not authorization:
        logger.warning("Webhook auth failed | missing Authorization header")
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    scheme = scheme.lower()
    if scheme == "basic":
        if _verify_basic_auth(token, settings):
            return
        logger.warning(
            "Webhook auth failed | scheme=basic username=%r",
            _basic_username(token),
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook credentials",
            headers={"WWW-Authenticate": 'Basic realm="MacroDroid"'},
        )
    if scheme == "bearer":
        if token == settings.macrodroid_webhook_secret:
            return
        logger.warning("Webhook auth failed | scheme=bearer")
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")
    logger.warning("Webhook auth failed | scheme=%r", scheme)
    raise HTTPException(status_code=401, detail="Unsupported authorization scheme")


def _account_name_match(name: str):
    """Match an account whose name equals the app name or contains it.

    Existing accounts follow the `Instituição-Pessoa` convention (e.g.
    "PicPay-Giovanni", "Mercado Pago-Débora"). A plain exact ``ilike`` never
    matches those, so the webhook used to auto-create a phantom account
    named exactly after the app. Matching by containment lets the webhook
    route to the user's real, named account.
    """
    name = (name or "").strip()
    if not name:
        return func.false()
    return or_(
        func.lower(Account.name) == name.lower(),
        Account.name.ilike(f"%{name}%"),
    )


def _account_name_order(banco_app: str, person_token: str = ""):
    """Order candidate accounts: exact app-name match first, then the
    sender's own account (name containing the person), then alphabetical."""
    order = [(func.lower(Account.name) == banco_app.lower()).desc()]
    if person_token:
        order.append(func.lower(Account.name).contains(person_token).desc())
    order.append(Account.name)
    return order


def _person_token(sender: str | None) -> str:
    """First normalized word of the sender, used to prefer their own account."""
    if not sender:
        return ""
    parts = _normalize_name(sender).split()
    return parts[0] if parts else ""


async def _resolve_account(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    banco_app: str,
    movement_type: str = "",
    card_last4: str | None = None,
    user_id: uuid.UUID | None = None,
    sender: str | None = None,
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
    person_token = _person_token(sender)
    conditions = [
        Account.workspace_id == workspace_id,
        Account.type == tipo_conta,
        _account_name_match(banco_app),
    ]
    if user_id:
        conditions.append(Account.user_id == user_id)
    result = await session.execute(
        select(Account)
        .where(*conditions)
        .order_by(*_account_name_order(banco_app, person_token))
        .limit(1)
    )
    account = result.scalar_one_or_none()
    if account:
        return account

    # 3) Fallback: default account of the app (first found by name, any type).
    conditions = [
        Account.workspace_id == workspace_id,
        _account_name_match(banco_app),
    ]
    if user_id:
        conditions.append(Account.user_id == user_id)
    result = await session.execute(
        select(Account)
        .where(*conditions)
        .order_by(*_account_name_order(banco_app, person_token))
        .limit(1)
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
        "text": "Você recebeu um Pix de R$ 30,00",
        "sender": "Giovanni",
        "descricao": "PIX ENVIADO DE NEON PARA PADARIA",
        "horario": "10/08/2026 14:32:05"
    }
    ```

    ``descricao`` (when present) is the authoritative display text: the app
    and beneficiary are extracted from it and it wins over the fields parsed
    from ``text``, which is only used for the amount. ``horario`` sets the
    transaction date (server date when absent). Duplicate notifications are
    ignored via a SHA-256 fingerprint of text + horario + descricao.
    """
    logger.info(
        "MacroDroid notification received | app=%s sender=%s descricao=%r",
        payload.app,
        payload.sender,
        payload.descricao,
    )

    # Parse the notification (value + movement come from the raw text).
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

    # Validate the notification timestamp before doing any work.
    try:
        notif_dt = _parse_horario(payload.horario)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if notif_dt:
        tx_date = notif_dt.date()
        time_str = notif_dt.strftime("%H:%M:%S")
    else:
        tx_date = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        time_str = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%H:%M:%S")

    # The MacroDroid-built `descricao` is authoritative: it carries the app,
    # the beneficiary and the movement in a readable form ("who paid whom").
    # `text` remains the only source for the amount.
    descricao_override = _parse_descricao(payload.descricao)
    if descricao_override:
        movement, app_from_desc, beneficiary = descricao_override
        banco_app = app_from_desc or parsed.banco_app
        beneficiary_missing = not beneficiary
        parsed = replace(
            parsed,
            banco_app=banco_app,
            movement_type=movement,
            tipo=MOVEMENT_TO_TIPO[movement],
            origem_destino=beneficiary or DESTINATARIO_NAO_INFORMADO,
            descricao=payload.descricao.strip(),
            # A well-formed `descricao` is authoritative (no review needed);
            # a blank beneficiary falls back to the placeholder + review.
            precisa_revisao=beneficiary_missing,
        )
        if beneficiary_missing:
            logger.warning(
                "MacroDroid descricao sem destinatário | app=%s | descricao=%r",
                banco_app,
                payload.descricao,
            )

    # Duplicate fingerprint (MacroDroid may deliver the same notification more
    # than once). Stored on the row and checked before insert.
    dedup_hash = _dedup_key(payload.text, payload.horario, payload.descricao)
    external_key = f"macrodroid:{dedup_hash}"

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

    # Idempotency: if this exact notification already produced a transaction,
    # return the existing one instead of creating a duplicate.
    from app.models.transaction import Transaction

    existing_result = await session.execute(
        select(Transaction.id).where(
            Transaction.workspace_id == workspace.id,
            Transaction.external_id == external_key,
        )
    )
    existing_id = existing_result.scalar_one_or_none()
    if existing_id:
        logger.info(
            "MacroDroid duplicate ignored | id=%s hash=%s", existing_id, dedup_hash
        )
        return {"ok": True, "transaction_id": str(existing_id), "duplicate": True}

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

    # Category is intentionally left unset — the user categorizes it later in
    # the UI. The payload's `categoria` field is ignored for now.
    category = None
    category_name = None

    # Resolve account — find or create, preferring the sender's own account.
    account = await _resolve_account(
        session,
        workspace.id,
        parsed.banco_app,
        movement_type=parsed.movement_type or "",
        card_last4=parsed.cartao_final,
        user_id=account_user_id,
        sender=payload.sender,
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

    # Notes: translated prefix + notification timestamp so the hour is visible.
    notes = f"{NOTES_FIXO} {tx_date.strftime('%d/%m/%Y')} {time_str}"

    # Beneficiary is intentionally left unset — the user fills it later in the
    # UI. The parser-extracted name/establishment stays only in the description.
    payee = None
    payee_id = None

    # Create the transaction
    from app.services.credit_card_service import apply_effective_date

    transaction = Transaction(
        user_id=owner_id,
        workspace_id=workspace.id,
        account_id=account.id,
        category_id=category.id if category else None,
        description=descricao,
        amount=parsed.valor,
        currency="BRL",
        date=tx_date,
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
        external_id=external_key,
        raw_data={
            "origem_destino": parsed.origem_destino,
            "movement_type": parsed.movement_type or None,
            "cartao_final": parsed.cartao_final,
            "needs_review": parsed.precisa_revisao,
            "estabelecimento": payload.estabelecimento,
            "categoria": payload.categoria,
            "notificacao_original": payload.text,
            "descricao": payload.descricao,
            "horario": payload.horario,
            "dedup_hash": dedup_hash,
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
