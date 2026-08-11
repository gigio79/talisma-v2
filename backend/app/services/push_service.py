"""Web Push (VAPID) delivery for due-date alerts.

The hourly Celery job and the on-demand ``/generate`` endpoint push a
notification to every browser/device the user has subscribed. Delivery goes
through the browser's push service (Google FCM on Android/Chrome) using VAPID
signing — no third-party account required.

Endpoints that respond 404/410 (unsubscribed / revoked) are pruned so the
table never accumulates dead rows.
"""
import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.models.workspace import WorkspaceMember

logger = logging.getLogger(__name__)

# Push services report a subscription is dead with these codes.
DEAD_ENDPOINT_STATUSES = (404, 410)

# A subscription created within this window is never pruned on 404/410. A brand
# new endpoint being rejected usually means a device/config problem, not an
# abandoned endpoint — pruning it would silently disable that user's pushes.
FRESH_SUBSCRIPTION_WINDOW = timedelta(hours=1)

# Days-before-due label shown in the push body, localized per recipient's
# language. Each entry maps the four alert types to the label in that language.
ALERT_LABELS_BY_LANG: dict[str, dict[str, str]] = {
    "pt-BR": {
        "7_DAYS": "Vence em 7 dias",
        "3_DAYS": "Vence em 3 dias",
        "1_DAY": "Vence amanhã",
        "DUE_DATE": "Vence hoje!",
    },
    "en": {
        "7_DAYS": "Due in 7 days",
        "3_DAYS": "Due in 3 days",
        "1_DAY": "Due tomorrow",
        "DUE_DATE": "Due today!",
    },
    "de": {
        "7_DAYS": "Fällig in 7 Tagen",
        "3_DAYS": "Fällig in 3 Tagen",
        "1_DAY": "Fällig morgen",
        "DUE_DATE": "Heute fällig!",
    },
    "es": {
        "7_DAYS": "Vence en 7 días",
        "3_DAYS": "Vence en 3 días",
        "1_DAY": "Vence mañana",
        "DUE_DATE": "¡Vence hoy!",
    },
    "it": {
        "7_DAYS": "Scade tra 7 giorni",
        "3_DAYS": "Scade tra 3 giorni",
        "1_DAY": "Scade domani",
        "DUE_DATE": "Scade oggi!",
    },
    "pl": {
        "7_DAYS": "Termin za 7 dni",
        "3_DAYS": "Termin za 3 dni",
        "1_DAY": "Termin jutro",
        "DUE_DATE": "Termin dziś!",
    },
    "ru": {
        "7_DAYS": "Срок через 7 дней",
        "3_DAYS": "Срок через 3 дня",
        "1_DAY": "Срок завтра",
        "DUE_DATE": "Срок сегодня!",
    },
    "uk": {
        "7_DAYS": "Термін через 7 днів",
        "3_DAYS": "Термін через 3 дні",
        "1_DAY": "Термін завтра",
        "DUE_DATE": "Термін сьогодні!",
    },
}

# Currency code → symbol used in the push body.
CURRENCY_SYMBOLS: dict[str, str] = {
    "BRL": "R$",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CAD": "C$",
    "AUD": "A$",
    "CHF": "CHF",
    "ARS": "$",
    "MXN": "$",
    "COP": "$",
    "PEN": "S/",
    "UYU": "$U",
    "CLP": "$",
}

# Languages that group thousands with "." and decimals with "," (1.234,56).
_DECIMAL_COMMA_LANGS = ("pt-BR", "pt", "de", "es", "it", "pl", "ru", "uk")

_FALLBACK_LABELS = {
    "7_DAYS": "Vencimento",
    "3_DAYS": "Vencimento",
    "1_DAY": "Vencimento",
    "DUE_DATE": "Vencimento",
}


def _normalize_language(language: str | None) -> str:
    """Map a user language preference to a supported label locale."""
    if not language:
        return "pt-BR"
    normalized = language.strip()
    if normalized in ALERT_LABELS_BY_LANG:
        return normalized
    base = normalized.split("-")[0].split("_")[0].lower()
    if base == "pt":
        return "pt-BR"
    if base == "en":
        return "en"
    return "pt-BR"


def vapid_configured() -> bool:
    """Web Push is opt-in: both VAPID keys must be set in the environment."""
    settings = get_settings()
    return bool(settings.vapid_public_key and settings.vapid_private_key)


def _format_amount(value, currency: str | None, language: str) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return ""
    symbol = CURRENCY_SYMBOLS.get(currency or "", "R$")
    int_part, dec_part = f"{amount:,.2f}".split(".")
    if language in _DECIMAL_COMMA_LANGS:
        return f"{symbol} {int_part.replace(',', '.')},{dec_part}"
    return f"{symbol} {int_part}.{dec_part}"


def build_payload(notification: Notification, language: str | None = None) -> dict:
    """Compose the push payload for one alert (title, body + deep-link data).

    ``language`` localizes the due-date label and amount formatting for the
    recipient (defaults to pt-BR — the app's original push locale).
    """
    lang = _normalize_language(language)
    title = notification.description or notification.account_name or "Talismã · Conta"
    labels = ALERT_LABELS_BY_LANG.get(lang, _FALLBACK_LABELS)
    parts = [labels.get(notification.alert_type, "Vencimento")]
    if notification.amount is not None:
        parts.append(_format_amount(notification.amount, notification.currency, lang))
    body = " · ".join(parts)
    return {
        "title": title,
        "body": body,
        "data": {
            "url": "/transactions",
            "notification_id": str(notification.id),
            "alert_type": notification.alert_type,
            "due_date": notification.due_date.isoformat(),
        },
    }


def _send_sync(subscription: PushSubscription, payload: dict) -> None:
    settings = get_settings()
    webpush(
        subscription_info={
            "endpoint": subscription.endpoint,
            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
        },
        data=json.dumps(payload),
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={"sub": settings.vapid_subject},
        timeout=10,
    )


def _response_text(response) -> str:
    """Best-effort body of a failed push response, for diagnostics."""
    text = getattr(response, "text", None)
    if text is not None:
        return text
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return ""


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize a stored timestamp to tz-aware UTC (some DB drivers return
    naive datetimes) so it can be compared against ``datetime.now(UTC)``."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def send_to_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    payload: dict,
) -> tuple[int, int]:
    """Send ``payload`` to every subscription of ``user_id``.

    Returns ``(sent, pruned)`` — the number of successful deliveries and the
    number of dead endpoints removed. Never raises: delivery errors are logged
    and isolated per subscription.
    """
    if not vapid_configured():
        return (0, 0)

    result = await session.execute(
        select(PushSubscription).where(PushSubscription.user_id == user_id)
    )
    subscriptions = list(result.scalars().all())
    if not subscriptions:
        return (0, 0)

    sent = 0
    pruned: list[uuid.UUID] = []
    fresh_cutoff = datetime.now(UTC) - FRESH_SUBSCRIPTION_WINDOW
    for sub in subscriptions:
        try:
            await asyncio.to_thread(_send_sync, sub, payload)
            sent += 1
        except WebPushException as exc:
            response = exc.response
            status_code = response.status_code if response is not None else None
            body = _response_text(response) if response is not None else ""
            if status_code in DEAD_ENDPOINT_STATUSES:
                created_at = _as_utc(sub.created_at)
                if created_at and created_at >= fresh_cutoff:
                    # Fresh endpoint rejected — a device/config problem, not a
                    # dead row. Keep it and surface the details loudly so the
                    # root cause is diagnosable instead of silently losing push.
                    logger.warning(
                        "Push rejected with %s for freshly-registered subscription "
                        "%s (user=%s, created=%s): %s",
                        status_code,
                        sub.id,
                        sub.user_id,
                        sub.created_at,
                        body,
                    )
                else:
                    pruned.append(sub.id)
            else:
                logger.warning(
                    "Push send failed for %s (user=%s): status=%s body=%s",
                    sub.endpoint,
                    sub.user_id,
                    status_code,
                    body,
                )
        except Exception as exc:  # noqa: BLE001 — network/timeout: never kill the batch
            logger.warning(
                "Push send failed for %s (user=%s): %s", sub.endpoint, sub.user_id, exc
            )

    if pruned:
        await session.execute(
            delete(PushSubscription).where(PushSubscription.id.in_(pruned))
        )
        await session.commit()

    return (sent, len(pruned))


async def upsert_subscription(
    session: AsyncSession,
    user_id: uuid.UUID,
    endpoint: str,
    p256dh: str,
    auth: str,
    device_label: str | None = None,
) -> PushSubscription:
    """Register or refresh a subscription. Uniqueness is scoped to
    ``(endpoint, user_id)``: re-registering the same endpoint for the same user
    updates keys in place, while two members sharing a browser each keep their
    own row so one can never hijack the other's pushes."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(PushSubscription).where(
            PushSubscription.endpoint == endpoint,
            PushSubscription.user_id == user_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.p256dh = p256dh
        existing.auth = auth
        existing.device_label = device_label
        existing.last_seen_at = now
    else:
        existing = PushSubscription(
            user_id=user_id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            device_label=device_label,
            last_seen_at=now,
        )
        session.add(existing)
    await session.commit()
    await session.refresh(existing)
    return existing


async def list_user_subscriptions(
    session: AsyncSession, user_id: uuid.UUID
) -> list[PushSubscription]:
    """Every subscription currently registered for ``user_id`` — lets the
    frontend reconcile the browser's subscription against the backend."""
    result = await session.execute(
        select(PushSubscription).where(PushSubscription.user_id == user_id)
    )
    return list(result.scalars().all())


async def remove_subscription(
    session: AsyncSession, subscription_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Remove a subscription (user revoked permission / unsubscribed)."""
    result = await session.execute(
        delete(PushSubscription).where(
            PushSubscription.id == subscription_id,
            PushSubscription.user_id == user_id,
        )
    )
    await session.commit()
    return (result.rowcount or 0) > 0


async def _workspace_member_ids(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[uuid.UUID]:
    """Every user that belongs to a workspace (deduplicated)."""
    result = await session.execute(
        select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == workspace_id
        )
    )
    return list(dict.fromkeys(result.scalars().all()))


async def _member_languages(
    session: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Resolve each member's display language from their preferences."""
    if not user_ids:
        return {}
    result = await session.execute(
        select(User.id, User.preferences).where(User.id.in_(user_ids))
    )
    languages: dict[uuid.UUID, str] = {}
    for user_id, preferences in result.all():
        language = (preferences or {}).get("language")
        languages[user_id] = _normalize_language(language)
    return languages


async def send_to_workspace(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    payload: dict,
) -> tuple[int, int]:
    """Send ``payload`` to every subscribed device of every workspace member.

    Returns ``(sent, pruned)``. Never raises: per-user delivery isolates
    failures and prunes dead endpoints.
    """
    if not vapid_configured():
        return (0, 0)

    member_ids = await _workspace_member_ids(session, workspace_id)
    if not member_ids:
        return (0, 0)

    sent = 0
    pruned = 0
    for member_id in member_ids:
        member_sent, member_pruned = await send_to_user(session, member_id, payload)
        sent += member_sent
        pruned += member_pruned
    return (sent, pruned)


async def send_notifications_push(
    session: AsyncSession,
    notifications: list[Notification],
) -> int:
    """Send one push per freshly-created alert to every member of the alert's
    workspace — so a family workspace notifies all devices, not just the owner
    of the account that triggered the alert. Returns number delivered."""
    by_workspace: dict[uuid.UUID, list[Notification]] = {}
    for notification in notifications:
        by_workspace.setdefault(notification.workspace_id, []).append(notification)

    total = 0
    for workspace_id, workspace_notifications in by_workspace.items():
        member_ids = await _workspace_member_ids(session, workspace_id)
        languages = await _member_languages(session, member_ids)
        for notification in workspace_notifications:
            for member_id in member_ids:
                payload = build_payload(notification, languages.get(member_id))
                sent, _ = await send_to_user(session, member_id, payload)
                total += sent
    return total
