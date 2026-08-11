import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.config import get_settings
from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.models.user import User
from app.schemas.notification import NotificationRead, UnreadCount
from app.schemas.push_subscription import (
    PushResult,
    PushSubscriptionCreate,
    PushSubscriptionRead,
    PushVapidKey,
)
from app.services import notification_service, push_service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/push-vapid-key", response_model=PushVapidKey)
async def push_vapid_key():
    settings = get_settings()
    return PushVapidKey(
        enabled=push_service.vapid_configured(),
        public_key=settings.vapid_public_key,
    )


@router.post("/push-subscription", response_model=PushSubscriptionRead)
async def subscribe_push(
    payload: PushSubscriptionCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    sub = await push_service.upsert_subscription(
        session,
        user.id,
        payload.endpoint,
        payload.p256dh,
        payload.auth,
        payload.device_label,
    )
    return PushSubscriptionRead.model_validate(sub)


@router.delete("/push-subscription/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe_push(
    subscription_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    if not await push_service.remove_subscription(session, subscription_id, user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found"
        )


@router.get("/push-subscription", response_model=list[PushSubscriptionRead])
async def list_push_subscriptions(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Every device the current user has subscribed — lets the frontend detect
    a browser subscription the backend no longer knows about (e.g. pruned)."""
    return await push_service.list_user_subscriptions(session, user.id)


@router.post("/push-test", response_model=PushResult)
async def push_test(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Send an immediate test push to every device of every workspace member."""
    sent, pruned = await push_service.send_to_workspace(
        session,
        ctx.workspace.id,
        {
            "title": "Talismã",
            "body": "Notificação push de teste!",
            "data": {"url": "/transactions", "test": True},
        },
    )
    return PushResult(sent=sent, pruned=pruned)


@router.get("", response_model=list[NotificationRead])
async def list_notifications(
    notification_status: str | None = Query(None, alias="status"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    if notification_status not in (None, "unread", "read", "dismissed"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status must be unread, read or dismissed",
        )
    return await notification_service.get_notifications(
        session, ctx.workspace.id, notification_status
    )


@router.get("/unread-count", response_model=UnreadCount)
async def notifications_unread_count(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    count = await notification_service.unread_count(session, ctx.workspace.id)
    return UnreadCount(count=count)


@router.post("/read-all", response_model=UnreadCount)
async def mark_all_notifications_read(
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    count = await notification_service.mark_all_read(session, ctx.workspace.id)
    return UnreadCount(count=count)


@router.post("/{notification_id}/read", response_model=NotificationRead)
async def mark_notification_read(
    notification_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    if not await notification_service.mark_read(session, notification_id, ctx.workspace.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    notification = await notification_service.get_notification(
        session, notification_id, ctx.workspace.id
    )
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    return notification


@router.post("/{notification_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_notification(
    notification_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    if not await notification_service.mark_dismissed(session, notification_id, ctx.workspace.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )


@router.post("/generate", response_model=UnreadCount)
async def generate_notifications_now(
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Manually trigger due-date alert generation for the current user
    (normally the hourly Celery task). Pushes newly created alerts to the
    user's subscribed devices. Returns alerts created."""
    created = await notification_service.create_due_date_alerts(session, ctx.user)
    if created:
        await push_service.send_notifications_push(session, created)
    return UnreadCount(count=len(created))
