"""Tests for the Web Push (VAPID) delivery service."""
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from pywebpush import WebPushException
from sqlalchemy import func, select

from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.services import push_service


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


async def _add_member(
    session, workspace, email="spouse@example.com", language="pt-BR"
) -> User:
    import bcrypt as _bcrypt

    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=_bcrypt.hashpw(b"testpass123", _bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=False,
        is_verified=True,
        preferences={"language": language, "timezone": "America/Sao_Paulo"},
    )
    session.add(user)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, role="editor"
        )
    )
    await session.commit()
    await session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_vapid_configured_false_without_keys():
    # Test env has no VAPID keys set — the feature must be reported off.
    assert push_service.vapid_configured() is False


@pytest.mark.asyncio
async def test_build_payload():
    n = Notification(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        target_type="transaction",
        target_id=uuid.uuid4(),
        alert_type="DUE_DATE",
        description="Conta de Internet",
        amount=Decimal("123.45"),
        currency="BRL",
        type="debit",
        account_name="Cartão D'M",
        due_date=date(2026, 8, 10),
    )
    payload = push_service.build_payload(n)
    assert payload["title"] == "Conta de Internet"
    assert payload["body"] == "Vence hoje! · R$ 123,45"
    assert payload["data"]["due_date"] == "2026-08-10"
    assert payload["data"]["url"] == "/transactions"

    # English recipient → localized label + decimal dot.
    payload_en = push_service.build_payload(n, language="en")
    assert payload_en["body"] == "Due today! · R$ 123.45"

    # German decimal style also uses a comma.
    payload_de = push_service.build_payload(n, language="de")
    assert payload_de["body"] == "Heute fällig! · R$ 123,45"

    # Unknown / unset language falls back to pt-BR (original push locale).
    assert push_service.build_payload(n, language="xx")["body"] == payload["body"]
    assert push_service.build_payload(n, language=None)["body"] == payload["body"]


@pytest.mark.asyncio
async def test_upsert_subscription_creates_and_updates(session, test_user):
    sub = await push_service.upsert_subscription(
        session,
        test_user.id,
        "https://fcm.googleapis.com/test/endpoint",
        "p256dh-key",
        "auth-key",
        "Pixel 8",
    )
    assert sub.user_id == test_user.id
    assert sub.endpoint == "https://fcm.googleapis.com/test/endpoint"

    # Same endpoint re-registered → row is refreshed, not duplicated.
    sub2 = await push_service.upsert_subscription(
        session, test_user.id, "https://fcm.googleapis.com/test/endpoint", "new-dh", "new-auth"
    )
    assert sub2.id == sub.id
    assert sub2.p256dh == "new-dh"

    count = await session.scalar(
        select(func.count())
        .select_from(PushSubscription)
        .where(PushSubscription.endpoint == "https://fcm.googleapis.com/test/endpoint")
    )
    assert count == 1


@pytest.mark.asyncio
async def test_upsert_subscription_keeps_users_separate(session, test_user, test_workspace):
    # The same endpoint (shared browser/device) registered by two members must
    # yield two rows — otherwise the second member hijacks the first's push.
    spouse = await _add_member(session, test_workspace)
    sub1 = await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/shared", "dh", "auth"
    )
    sub2 = await push_service.upsert_subscription(
        session, spouse.id, "https://endpoint/shared", "dh", "auth"
    )
    assert sub1.id != sub2.id
    assert sub1.user_id == test_user.id
    assert sub2.user_id == spouse.id

    count = await session.scalar(
        select(func.count())
        .select_from(PushSubscription)
        .where(PushSubscription.endpoint == "https://endpoint/shared")
    )
    assert count == 2


@pytest.mark.asyncio
async def test_list_user_subscriptions(session, test_user):
    assert await push_service.list_user_subscriptions(session, test_user.id) == []
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/list-1", "dh", "auth"
    )
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/list-2", "dh2", "auth2"
    )
    subs = await push_service.list_user_subscriptions(session, test_user.id)
    assert {s.endpoint for s in subs} == {"https://endpoint/list-1", "https://endpoint/list-2"}


@pytest.mark.asyncio
async def test_remove_subscription(session, test_user):
    sub = await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/x", "dh", "auth"
    )
    assert await push_service.remove_subscription(session, sub.id, test_user.id) is True
    # Wrong user → not removable
    sub = await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/y", "dh", "auth"
    )
    assert await push_service.remove_subscription(session, sub.id, uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_send_to_user_success(session, test_user):
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/success", "dh", "auth"
    )
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/success2", "dh2", "auth2"
    )
    with patch.object(push_service, "vapid_configured", return_value=True), patch.object(
        push_service, "webpush"
    ) as mock_webpush:
        sent, pruned = await push_service.send_to_user(
            session, test_user.id, {"title": "x", "body": "y"}
        )
    assert sent == 2
    assert pruned == 0
    assert mock_webpush.call_count == 2


@pytest.mark.asyncio
async def test_send_to_user_prunes_dead_endpoints(session, test_user):
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/dead", "dh", "auth"
    )
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/alive", "dh", "auth"
    )

    # Age both subscriptions past the fresh window so 404/410 pruning applies.
    aged = datetime.now(UTC) - timedelta(hours=2)
    for sub in (
        await session.scalars(
            select(PushSubscription).where(PushSubscription.user_id == test_user.id)
        )
    ).all():
        sub.created_at = aged
        sub.last_seen_at = aged
    await session.commit()

    def _raise_gone(*args, **kwargs):
        if "dead" in kwargs["subscription_info"]["endpoint"]:
            raise WebPushException("gone", response=_FakeResponse(410))
        return MagicMock()
    with patch.object(push_service, "vapid_configured", return_value=True), patch.object(
        push_service, "webpush", side_effect=_raise_gone
    ):
        sent, pruned = await push_service.send_to_user(session, test_user.id, {"title": "x"})
    assert sent == 1  # the alive endpoint still delivered
    assert pruned == 1

    remaining = (
        await session.scalars(
            select(PushSubscription).where(PushSubscription.user_id == test_user.id)
        )
    ).all()
    assert len(remaining) == 1
    assert remaining[0].endpoint == "https://endpoint/alive"


@pytest.mark.asyncio
async def test_send_to_user_keeps_fresh_dead_endpoints(session, test_user):
    # A brand-new endpoint rejected with 404/410 is a device/config problem,
    # not an abandoned subscription — it must NOT be pruned (that would
    # silently disable the user's pushes forever).
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/fresh-dead", "dh", "auth"
    )

    def _raise_gone(*args, **kwargs):
        raise WebPushException("gone", response=_FakeResponse(410))
    with patch.object(push_service, "vapid_configured", return_value=True), patch.object(
        push_service, "webpush", side_effect=_raise_gone
    ):
        sent, pruned = await push_service.send_to_user(session, test_user.id, {"title": "x"})
    assert (sent, pruned) == (0, 0)

    remaining = (
        await session.scalars(
            select(PushSubscription).where(PushSubscription.user_id == test_user.id)
        )
    ).all()
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_send_to_user_isolates_errors(session, test_user):
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/bad", "dh", "auth"
    )
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/good", "dh", "auth"
    )

    def _flaky(*args, **kwargs):
        if "bad" in kwargs["subscription_info"]["endpoint"]:
            raise WebPushException("boom", response=_FakeResponse(500))
        return MagicMock()

    with patch.object(push_service, "vapid_configured", return_value=True), patch.object(
        push_service, "webpush", side_effect=_flaky
    ):
        sent, pruned = await push_service.send_to_user(session, test_user.id, {"title": "x"})
    assert sent == 1  # the 500 one failed but didn't kill the batch
    assert pruned == 0


@pytest.mark.asyncio
async def test_send_to_user_logs_fcm_error_body(session, test_user, caplog):
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/body", "dh", "auth"
    )

    class _Body(_FakeResponse):
        text = "token not registered"

    def _raise(*args, **kwargs):
        raise WebPushException("gone", response=_Body(500))
    with caplog.at_level("WARNING", logger="app.services.push_service"), patch.object(
        push_service, "vapid_configured", return_value=True
    ), patch.object(push_service, "webpush", side_effect=_raise):
        sent, pruned = await push_service.send_to_user(session, test_user.id, {"title": "x"})
    assert (sent, pruned) == (0, 0)
    assert any("token not registered" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_send_notifications_push_batches(session, test_user, test_workspace):
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/batch", "dh", "auth"
    )
    notifications = [
        Notification(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=uuid.uuid4(),
            target_type="transaction",
            target_id=uuid.uuid4(),
            alert_type="3_DAYS",
            description=f"Conta {i}",
            due_date=date(2026, 8, 10),
        )
        for i in range(3)
    ]
    with patch.object(push_service, "vapid_configured", return_value=True), patch.object(
        push_service, "webpush"
    ) as mock_webpush:
        sent = await push_service.send_notifications_push(session, notifications)
    assert sent == 3
    assert mock_webpush.call_count == 3


@pytest.mark.asyncio
async def test_send_to_workspace_reaches_all_members(
    session, test_user, test_workspace
):
    spouse = await _add_member(session, test_workspace)
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/owner", "dh", "auth"
    )
    await push_service.upsert_subscription(
        session, spouse.id, "https://endpoint/spouse", "dh", "auth"
    )
    with patch.object(push_service, "vapid_configured", return_value=True), patch.object(
        push_service, "webpush"
    ) as mock_webpush:
        sent, pruned = await push_service.send_to_workspace(
            session, test_workspace.id, {"title": "x"}
        )
    assert (sent, pruned) == (2, 0)
    assert mock_webpush.call_count == 2


@pytest.mark.asyncio
async def test_send_notifications_push_reaches_all_members(
    session, test_user, test_workspace
):
    spouse = await _add_member(session, test_workspace)
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/owner2", "dh", "auth"
    )
    await push_service.upsert_subscription(
        session, spouse.id, "https://endpoint/spouse2", "dh", "auth"
    )
    notifications = [
        Notification(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=uuid.uuid4(),
            target_type="transaction",
            target_id=uuid.uuid4(),
            alert_type="DUE_DATE",
            description="Fatura",
            due_date=date(2026, 8, 10),
        )
    ]
    with patch.object(push_service, "vapid_configured", return_value=True), patch.object(
        push_service, "webpush"
    ) as mock_webpush:
        sent = await push_service.send_notifications_push(session, notifications)
    # Both the owner and the other member get the push, even though only the
    # owner triggered it.
    assert sent == 2
    assert mock_webpush.call_count == 2


@pytest.mark.asyncio
async def test_send_notifications_push_localizes_per_member(
    session, test_user, test_workspace
):
    spouse = await _add_member(session, test_workspace, language="en")
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/owner-lang", "dh", "auth"
    )
    await push_service.upsert_subscription(
        session, spouse.id, "https://endpoint/spouse-lang", "dh", "auth"
    )
    notification = Notification(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=uuid.uuid4(),
        target_type="transaction",
        target_id=uuid.uuid4(),
        alert_type="DUE_DATE",
        description="Fatura",
        amount=Decimal("123.45"),
        currency="BRL",
        due_date=date(2026, 8, 10),
    )
    with patch.object(push_service, "vapid_configured", return_value=True), patch.object(
        push_service, "webpush"
    ) as mock_webpush:
        sent = await push_service.send_notifications_push(session, [notification])
    assert sent == 2
    bodies = {
        json.loads(call.kwargs["data"])["body"]
        for call in mock_webpush.call_args_list
    }
    assert bodies == {"Vence hoje! · R$ 123,45", "Due today! · R$ 123.45"}


@pytest.mark.asyncio
async def test_send_to_workspace_noop_when_disabled(session, test_user, test_workspace):
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/off-ws", "dh", "auth"
    )
    with patch.object(push_service, "vapid_configured", return_value=False), patch.object(
        push_service, "webpush"
    ) as mock_webpush:
        sent, pruned = await push_service.send_to_workspace(
            session, test_workspace.id, {"title": "x"}
        )
    assert (sent, pruned) == (0, 0)
    mock_webpush.assert_not_called()


@pytest.mark.asyncio
async def test_send_to_user_noop_when_disabled(session, test_user):
    await push_service.upsert_subscription(
        session, test_user.id, "https://endpoint/off", "dh", "auth"
    )
    with patch.object(push_service, "vapid_configured", return_value=False), patch.object(
        push_service, "webpush"
    ) as mock_webpush:
        sent, pruned = await push_service.send_to_user(session, test_user.id, {"title": "x"})
    assert (sent, pruned) == (0, 0)
    mock_webpush.assert_not_called()
