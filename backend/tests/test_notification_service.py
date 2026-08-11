"""Tests for the due-date notification service."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.credit_card_bill import CreditCardBill
from app.models.notification import Notification
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services.notification_service import (
    _user_today,
    generate_due_date_alerts,
    get_notifications,
    mark_all_read,
    mark_dismissed,
    mark_read,
    unread_count,
)


@pytest_asyncio.fixture
async def notif_account(session: AsyncSession, test_user) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Conta Principal",
        type="checking",
        balance=Decimal("1000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _make_category(session: AsyncSession, test_user, name: str, **kw) -> Category:
    cat = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name=name,
        icon="x",
        color="#000000",
        is_system=True,
        **kw,
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


async def _scheduled_tx(
    session: AsyncSession,
    test_user,
    account: Account,
    *,
    due_in: int,
    description: str = "Conta de Luz",
    amount: str = "150.00",
    status: str = "scheduled",
    with_due_date: bool = True,
    category_id=None,
) -> Transaction:
    today = _user_today(test_user)
    due = today + timedelta(days=due_in)
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        category_id=category_id,
        description=description,
        amount=Decimal(amount),
        currency="BRL",
        date=due,
        due_date=due if with_due_date else None,
        type="debit",
        source="manual",
        status=status,
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


async def _notifs(session: AsyncSession, test_workspace) -> list[Notification]:
    return await get_notifications(session, test_workspace.id)


# ---------------------------------------------------------------------------
# Generation — trigger days
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_fires_7_3_1_0_and_skips_others(
    session, test_user, test_workspace, notif_account
):
    await _scheduled_tx(session, test_user, notif_account, due_in=7, description="7d")
    await _scheduled_tx(session, test_user, notif_account, due_in=3, description="3d")
    await _scheduled_tx(session, test_user, notif_account, due_in=1, description="1d")
    await _scheduled_tx(session, test_user, notif_account, due_in=0, description="0d")
    # Outside the réguia — must NOT fire.
    await _scheduled_tx(session, test_user, notif_account, due_in=5, description="5d")
    await _scheduled_tx(session, test_user, notif_account, due_in=10, description="10d")
    # Already paid — must NOT fire.
    await _scheduled_tx(
        session, test_user, notif_account, due_in=2, status="posted", description="pago"
    )

    created = await generate_due_date_alerts(session, test_user)
    assert created == 4

    notifs = await _notifs(session, test_workspace)
    by_desc = {n.description: n.alert_type for n in notifs}
    assert by_desc == {
        "7d": "7_DAYS",
        "3d": "3_DAYS",
        "1d": "1_DAY",
        "0d": "DUE_DATE",
    }


@pytest.mark.asyncio
async def test_generate_uses_due_date_fallback_for_placeholders(
    session, test_user, test_workspace, notif_account
):
    """Placeholders from generate_pending have due_date=NULL — the alert must
    still fire using `date` as the vencimento."""
    await _scheduled_tx(
        session, test_user, notif_account, due_in=3, with_due_date=False
    )

    created = await generate_due_date_alerts(session, test_user)
    assert created == 1
    notifs = await _notifs(session, test_workspace)
    assert notifs[0].alert_type == "3_DAYS"


@pytest.mark.asyncio
async def test_generate_is_idempotent(
    session, test_user, test_workspace, notif_account
):
    await _scheduled_tx(session, test_user, notif_account, due_in=3)
    assert await generate_due_date_alerts(session, test_user) == 1
    # Second run must not duplicate.
    assert await generate_due_date_alerts(session, test_user) == 0
    assert len(await _notifs(session, test_workspace)) == 1


@pytest.mark.asyncio
async def test_generate_skips_transfer_and_ignored_categories(
    session, test_user, test_workspace, notif_account
):
    transfer = await _make_category(session, test_user, "Transfer", treat_as_transfer=True)
    ignored = await _make_category(session, test_user, "Ignored", is_ignored=True)
    await _scheduled_tx(
        session, test_user, notif_account, due_in=3, description="transfer",
        category_id=transfer.id,
    )
    await _scheduled_tx(
        session, test_user, notif_account, due_in=3, description="ignored",
        category_id=ignored.id,
    )

    assert await generate_due_date_alerts(session, test_user) == 0


@pytest.mark.asyncio
async def test_generate_from_credit_card_bill(
    session, test_user, test_workspace, notif_account
):
    today = _user_today(test_user)
    bill = CreditCardBill(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=notif_account.id,
        external_id="bill-1",
        due_date=today + timedelta(days=3),
        total_amount=Decimal("987.65"),
        currency="BRL",
    )
    session.add(bill)
    await session.commit()

    assert await generate_due_date_alerts(session, test_user) == 1
    notifs = await _notifs(session, test_workspace)
    n = notifs[0]
    assert n.target_type == "bill"
    assert n.alert_type == "3_DAYS"
    assert n.amount == Decimal("987.65")
    assert "Fatura" in n.description
    assert n.account_name == "Conta Principal"


@pytest.mark.asyncio
async def test_generate_from_recurring_projection(
    session, test_user, test_workspace, notif_account
):
    today = _user_today(test_user)
    rec = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=notif_account.id,
        description="Aluguel",
        amount=Decimal("2000"),
        currency="BRL",
        type="debit",
        frequency="monthly",
        start_date=today - timedelta(days=30),
        next_occurrence=today + timedelta(days=3),
        is_active=True,
        auto_generate=False,
    )
    session.add(rec)
    await session.commit()

    assert await generate_due_date_alerts(session, test_user) == 1
    notifs = await _notifs(session, test_workspace)
    n = notifs[0]
    assert n.target_type == "recurring"
    assert n.alert_type == "3_DAYS"
    assert n.due_date == today + timedelta(days=3)


@pytest.mark.asyncio
async def test_recurring_projection_skipped_when_materialized(
    session, test_user, test_workspace, notif_account
):
    """When the occurrence is already materialized as a scheduled placeholder,
    no *recurring* alert is created — the scheduled transaction itself is the
    single alert (via the transaction source)."""
    today = _user_today(test_user)
    rec = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=notif_account.id,
        description="Aluguel",
        amount=Decimal("2000"),
        currency="BRL",
        type="debit",
        frequency="monthly",
        start_date=today - timedelta(days=30),
        next_occurrence=today + timedelta(days=3),
        is_active=True,
        auto_generate=False,
    )
    session.add(rec)
    await session.commit()

    await _scheduled_tx(
        session, test_user, notif_account, due_in=3, with_due_date=False
    )
    # Link the placeholder to the recurring (as generate_pending would).
    placeholder = (
        await session.execute(select(Transaction).where(Transaction.status == "scheduled"))
    ).scalar_one()
    placeholder.recurring_transaction_id = rec.id
    await session.commit()

    assert await generate_due_date_alerts(session, test_user) == 1
    notifs = await _notifs(session, test_workspace)
    assert len(notifs) == 1
    assert notifs[0].target_type == "transaction"


# ---------------------------------------------------------------------------
# Status lifecycle + stale cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_read_dismiss_read_all(
    session, test_user, test_workspace, notif_account
):
    await _scheduled_tx(session, test_user, notif_account, due_in=3, description="A")
    await _scheduled_tx(session, test_user, notif_account, due_in=1, description="B")
    await generate_due_date_alerts(session, test_user)

    assert await unread_count(session, test_workspace.id) == 2

    n = (await _notifs(session, test_workspace))[0]
    assert await mark_read(session, n.id, test_workspace.id) is True
    assert await mark_read(session, n.id, test_workspace.id) is False  # already read
    assert await unread_count(session, test_workspace.id) == 1

    n2 = (await _notifs(session, test_workspace))[0]
    assert await mark_dismissed(session, n2.id, test_workspace.id) is True
    assert len(await get_notifications(session, test_workspace.id)) == 1  # dismissed hidden

    # read-all marks the last unread one
    assert await mark_all_read(session, test_workspace.id) == 1
    assert await unread_count(session, test_workspace.id) == 0


@pytest.mark.asyncio
async def test_paid_transaction_dismisses_its_alert(
    session, test_user, test_workspace, notif_account
):
    tx = await _scheduled_tx(session, test_user, notif_account, due_in=3)
    await generate_due_date_alerts(session, test_user)
    assert await unread_count(session, test_workspace.id) == 1

    tx.status = "posted"
    await session.commit()

    # Next run auto-dismisses the stale alert.
    await generate_due_date_alerts(session, test_user)
    assert await unread_count(session, test_workspace.id) == 0
    assert len(await get_notifications(session, test_workspace.id)) == 0


# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------


def test_user_today_uses_timezone_preference():
    class FakeUser:
        preferences = {"timezone": "UTC"}

    import datetime as _dt
    from zoneinfo import ZoneInfo

    now_utc = _dt.datetime.now(ZoneInfo("UTC")).date()
    assert _user_today(FakeUser()) == now_utc
