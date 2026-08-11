"""Tests for the notifications API."""
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.services.notification_service import _user_today


@pytest_asyncio.fixture
async def notif_api_account(session: AsyncSession, test_user) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="API Conta",
        type="checking",
        balance=Decimal(500),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _seed_scheduled_tx(
    session: AsyncSession, test_user, account: Account, due_in: int
) -> Transaction:
    due = _user_today(test_user) + timedelta(days=due_in)
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        description="Conta de Internet",
        amount=Decimal("99.90"),
        currency="BRL",
        date=due,
        due_date=due,
        type="debit",
        source="manual",
        status="scheduled",
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


@pytest.mark.asyncio
async def test_notifications_require_auth(client):
    response = await client.get("/api/notifications")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_generate_list_count_lifecycle(
    client, auth_headers, session, test_user, notif_api_account
):
    await _seed_scheduled_tx(session, test_user, notif_api_account, due_in=3)
    await _seed_scheduled_tx(session, test_user, notif_api_account, due_in=1)

    gen = await client.post("/api/notifications/generate", headers=auth_headers)
    assert gen.status_code == 200
    assert gen.json()["count"] == 2
    # Idempotent
    gen2 = await client.post("/api/notifications/generate", headers=auth_headers)
    assert gen2.json()["count"] == 0

    count = await client.get("/api/notifications/unread-count", headers=auth_headers)
    assert count.status_code == 200
    assert count.json()["count"] == 2

    listing = await client.get("/api/notifications", headers=auth_headers)
    assert listing.status_code == 200
    items = listing.json()
    assert len(items) == 2
    # unread first; newest first within the same status
    assert items[0]["status"] == "unread"
    assert {i["alert_type"] for i in items} == {"3_DAYS", "1_DAY"}
    first = items[0]
    assert first["description"] == "Conta de Internet"
    assert first["amount"] == "99.90"
    assert first["account_name"] == "API Conta"

    # Mark one as read
    mark = await client.post(
        f"/api/notifications/{first['id']}/read", headers=auth_headers
    )
    assert mark.status_code == 200
    assert mark.json()["status"] == "read"

    count = await client.get("/api/notifications/unread-count", headers=auth_headers)
    assert count.json()["count"] == 1

    # Dismiss the remaining
    remaining = await client.get(
        "/api/notifications", params={"status": "unread"}, headers=auth_headers
    )
    assert len(remaining.json()) == 1
    dismiss = await client.post(
        f"/api/notifications/{remaining.json()[0]['id']}/dismiss",
        headers=auth_headers,
    )
    assert dismiss.status_code == 204

    listing = await client.get("/api/notifications", headers=auth_headers)
    assert len(listing.json()) == 1  # the read one remains
    final_count = await client.get("/api/notifications/unread-count", headers=auth_headers)
    assert final_count.json()["count"] == 0


@pytest.mark.asyncio
async def test_read_all(
    client, auth_headers, session, test_user, notif_api_account
):
    await _seed_scheduled_tx(session, test_user, notif_api_account, due_in=1)
    await _seed_scheduled_tx(session, test_user, notif_api_account, due_in=0)
    assert (await client.post("/api/notifications/generate", headers=auth_headers)).json()["count"] == 2

    resp = await client.post("/api/notifications/read-all", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["count"] == 2

    count = await client.get("/api/notifications/unread-count", headers=auth_headers)
    assert count.json()["count"] == 0


@pytest.mark.asyncio
async def test_invalid_status_filter_rejected(client, auth_headers):
    resp = await client.get(
        "/api/notifications", params={"status": "bogus"}, headers=auth_headers
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_mark_missing_notification_404(client, auth_headers):
    resp = await client.post(
        f"/api/notifications/{uuid.uuid4()}/read", headers=auth_headers
    )
    assert resp.status_code == 404
    resp = await client.post(
        f"/api/notifications/{uuid.uuid4()}/dismiss", headers=auth_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dismissed_status_filter_returns_dismissed(
    client, auth_headers, session, test_user, notif_api_account
):
    await _seed_scheduled_tx(session, test_user, notif_api_account, due_in=0)
    await _seed_scheduled_tx(session, test_user, notif_api_account, due_in=1)
    assert (await client.post("/api/notifications/generate", headers=auth_headers)).json()["count"] == 2

    listing = await client.get("/api/notifications", headers=auth_headers)
    first = listing.json()[0]["id"]
    assert (await client.post(f"/api/notifications/{first}/dismiss", headers=auth_headers)).status_code == 204

    # The default list hides dismissed rows but an explicit filter surfaces them.
    default_list = await client.get("/api/notifications", headers=auth_headers)
    assert all(n["status"] != "dismissed" for n in default_list.json())
    dismissed = await client.get(
        "/api/notifications", params={"status": "dismissed"}, headers=auth_headers
    )
    assert dismissed.status_code == 200
    assert len(dismissed.json()) == 1
    assert dismissed.json()[0]["id"] == first
    assert dismissed.json()[0]["status"] == "dismissed"


@pytest.mark.asyncio
async def test_push_subscription_lifecycle(client, auth_headers):
    # VAPID key endpoint is public and reports the feature state.
    key = await client.get("/api/notifications/push-vapid-key")
    assert key.status_code == 200
    assert key.json()["enabled"] is False  # no keys in the test env

    # Subscribe
    resp = await client.post(
        "/api/notifications/push-subscription",
        headers=auth_headers,
        json={
            "endpoint": "https://fcm.googleapis.com/test/api-sub",
            "p256dh": "p256dh-value",
            "auth": "auth-value",
            "device_label": "Pixel 8",
        },
    )
    assert resp.status_code == 200
    sub_id = resp.json()["id"]
    assert resp.json()["device_label"] == "Pixel 8"

    # Re-subscribe same endpoint → same id (idempotent)
    resp2 = await client.post(
        "/api/notifications/push-subscription",
        headers=auth_headers,
        json={
            "endpoint": "https://fcm.googleapis.com/test/api-sub",
            "p256dh": "p256dh-new",
            "auth": "auth-new",
        },
    )
    assert resp2.status_code == 200
    assert resp2.json()["id"] == sub_id

    # Test push — feature disabled → nothing sent, no crash
    test = await client.post("/api/notifications/push-test", headers=auth_headers)
    assert test.status_code == 200
    assert test.json() == {"sent": 0, "pruned": 0}

    # Unsubscribe
    gone = await client.delete(
        f"/api/notifications/push-subscription/{sub_id}", headers=auth_headers
    )
    assert gone.status_code == 204
    gone2 = await client.delete(
        f"/api/notifications/push-subscription/{sub_id}", headers=auth_headers
    )
    assert gone2.status_code == 404


@pytest.mark.asyncio
async def test_push_subscription_list_and_requires_auth(client, auth_headers):
    unauth = await client.get("/api/notifications/push-subscription")
    assert unauth.status_code == 401

    resp = await client.get("/api/notifications/push-subscription", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []

    await client.post(
        "/api/notifications/push-subscription",
        headers=auth_headers,
        json={
            "endpoint": "https://fcm.googleapis.com/test/list-sub",
            "p256dh": "p256dh-value",
            "auth": "auth-value",
            "device_label": "Pixel 8",
        },
    )
    resp = await client.get("/api/notifications/push-subscription", headers=auth_headers)
    items = resp.json()
    assert len(items) == 1
    assert items[0]["endpoint"] == "https://fcm.googleapis.com/test/list-sub"
    assert items[0]["device_label"] == "Pixel 8"


@pytest.mark.asyncio
async def test_push_subscription_requires_auth(client):
    resp = await client.post(
        "/api/notifications/push-subscription",
        json={"endpoint": "https://x", "p256dh": "dh", "auth": "auth"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_push_test_is_workspace_wide(client, auth_headers, test_workspace):
    from unittest.mock import AsyncMock, patch

    from app.services import push_service

    with patch.object(
        push_service, "send_to_workspace", new=AsyncMock(return_value=(2, 1))
    ) as mock_send:
        resp = await client.post("/api/notifications/push-test", headers=auth_headers)
        # Delivery is scoped to the current workspace, not the caller's account.
        assert mock_send.await_args.args[1] == test_workspace.id
    assert resp.status_code == 200
    assert resp.json() == {"sent": 2, "pruned": 1}
