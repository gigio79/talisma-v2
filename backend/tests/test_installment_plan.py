import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.schemas.transaction import InstallmentPlanCreate, TransactionCreate
from app.services.transaction_service import (
    create_installment_plan,
    create_transaction,
)


@pytest_asyncio.fixture
async def cc_account(session: AsyncSession, test_user) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.uuid4(),  # will be overridden by test_workspace
        name="Credit Card",
        type="credit_card",
        balance=Decimal("-500.00"),
        currency="BRL",
        statement_close_day=20,
        payment_due_day=10,
    )
    session.add(account)
    await session.flush()
    return account


@pytest_asyncio.fixture
async def cc_account_in_workspace(session: AsyncSession, test_user, test_workspace) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Nubank",
        type="credit_card",
        balance=Decimal("-500.00"),
        currency="BRL",
        statement_close_day=20,
        payment_due_day=10,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


# ---------------------------------------------------------------------------
# create_installment_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_installment_plan_basic(
    session: AsyncSession, test_user, test_workspace, test_categories, cc_account_in_workspace
):
    data = InstallmentPlanCreate(
        account_id=cc_account_in_workspace.id,
        description="TV Samsung 55\"",
        total_amount=Decimal("6000.00"),
        num_installments=12,
        purchase_date=date(2026, 7, 24),
        category_id=test_categories[0].id,
    )
    txns = await create_installment_plan(session, test_workspace.id, test_user.id, data)

    assert len(txns) == 12
    for i, tx in enumerate(txns, start=1):
        assert tx.installment_number == i
        assert tx.total_installments == 12
        assert tx.installment_total_amount == Decimal("6000.00")
        assert tx.installment_purchase_date == date(2026, 7, 24)
        assert tx.source == "manual"
        assert tx.type == "debit"
        assert tx.account_id == cc_account_in_workspace.id
        assert tx.category_id == test_categories[0].id
        assert tx.description == 'TV Samsung 55"'

    # First 11 installments: 500.00 each
    for tx in txns[:11]:
        assert tx.amount == Decimal("500.00")

    # Last installment absorbs rounding remainder (500 * 11 = 5500, so last = 500.00)
    assert txns[-1].amount == Decimal("500.00")


@pytest.mark.asyncio
async def test_create_installment_plan_rounding(
    session: AsyncSession, test_user, test_workspace, test_categories, cc_account_in_workspace
):
    """When total / n doesn't divide evenly, the last parcela absorbs the remainder."""
    data = InstallmentPlanCreate(
        account_id=cc_account_in_workspace.id,
        description="Notebook",
        total_amount=Decimal("1000.00"),
        num_installments=3,
        purchase_date=date(2026, 8, 1),
        category_id=test_categories[0].id,
    )
    txns = await create_installment_plan(session, test_workspace.id, test_user.id, data)

    assert len(txns) == 3
    amounts = [tx.amount for tx in txns]
    # 1000 / 3 = 333.333... → 333.33, 333.33, 333.34
    assert amounts[0] == Decimal("333.33")
    assert amounts[1] == Decimal("333.33")
    assert amounts[2] == Decimal("333.34")
    assert sum(amounts) == Decimal("1000.00")


@pytest.mark.asyncio
async def test_create_installment_plan_monthly_dates(
    session: AsyncSession, test_user, test_workspace, test_categories, cc_account_in_workspace
):
    """Each installment date advances by one month."""
    data = InstallmentPlanCreate(
        account_id=cc_account_in_workspace.id,
        description="Curso",
        total_amount=Decimal("1200.00"),
        num_installments=4,
        purchase_date=date(2026, 1, 31),
        category_id=test_categories[0].id,
    )
    txns = await create_installment_plan(session, test_workspace.id, test_user.id, data)

    expected_dates = [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]
    for tx, expected in zip(txns, expected_dates):
        assert tx.date == expected


@pytest.mark.asyncio
async def test_create_installment_plan_wrong_account_type(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    """Installment plans require a credit-card account."""
    checking = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Checking",
        type="checking",
        balance=Decimal("1000.00"),
        currency="BRL",
    )
    session.add(checking)
    await session.commit()

    data = InstallmentPlanCreate(
        account_id=checking.id,
        description="Should fail",
        total_amount=Decimal("1000.00"),
        num_installments=2,
        purchase_date=date(2026, 1, 1),
    )
    with pytest.raises(ValueError, match="credit-card"):
        await create_installment_plan(session, test_workspace.id, test_user.id, data)


@pytest.mark.asyncio
async def test_create_installment_plan_with_notes(
    session: AsyncSession, test_user, test_workspace, test_categories, cc_account_in_workspace
):
    data = InstallmentPlanCreate(
        account_id=cc_account_in_workspace.id,
        description="Celular",
        total_amount=Decimal("3000.00"),
        num_installments=6,
        purchase_date=date(2026, 6, 15),
        notes="Parcelamento sem juros",
    )
    txns = await create_installment_plan(session, test_workspace.id, test_user.id, data)

    for tx in txns:
        assert tx.notes == "Parcelamento sem juros"


@pytest.mark.asyncio
async def test_create_installment_plan_with_effective_bill_date(
    session: AsyncSession, test_user, test_workspace, test_categories, cc_account_in_workspace
):
    data = InstallmentPlanCreate(
        account_id=cc_account_in_workspace.id,
        description="Sofá",
        total_amount=Decimal("2400.00"),
        num_installments=12,
        purchase_date=date(2026, 3, 10),
        effective_bill_date=date(2026, 4, 10),
    )
    txns = await create_installment_plan(session, test_workspace.id, test_user.id, data)

    for tx in txns:
        assert tx.effective_bill_date == date(2026, 4, 10)
        assert tx.effective_date == date(2026, 4, 10)


# ---------------------------------------------------------------------------
# create_transaction with installment metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_transaction_with_installment_fields(
    session: AsyncSession, test_user, test_workspace, test_categories, cc_account_in_workspace
):
    data = TransactionCreate(
        description="Parcela 1/3",
        amount=Decimal("333.33"),
        date=date(2026, 8, 1),
        type="debit",
        account_id=cc_account_in_workspace.id,
        category_id=test_categories[0].id,
        installment_number=1,
        total_installments=3,
        installment_total_amount=Decimal("1000.00"),
        installment_purchase_date=date(2026, 7, 15),
    )
    tx = await create_transaction(session, test_workspace.id, test_user.id, data)

    assert tx.installment_number == 1
    assert tx.total_installments == 3
    assert tx.installment_total_amount == Decimal("1000.00")
    assert tx.installment_purchase_date == date(2026, 7, 15)


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_installments_api_endpoint(
    client, auth_headers, test_user, test_workspace, test_categories, cc_account_in_workspace
):
    payload = {
        "account_id": str(cc_account_in_workspace.id),
        "description": "PlayStation 5",
        "total_amount": "5000.00",
        "num_installments": 10,
        "purchase_date": "2026-09-01",
        "category_id": str(test_categories[0].id),
    }
    response = await client.post(
        "/api/transactions/installments",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert len(data) == 10
    for i, tx in enumerate(data, start=1):
        assert tx["installment_number"] == i
        assert tx["total_installments"] == 10
        assert tx["description"] == "PlayStation 5"


@pytest.mark.asyncio
async def test_installments_api_rejects_non_cc(
    client, auth_headers, test_user, test_workspace, test_categories
):
    checking = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Checking",
        type="checking",
        balance=Decimal("5000.00"),
        currency="BRL",
    )
    from tests.conftest import TestSessionLocal
    async with TestSessionLocal() as session:
        session.add(checking)
        await session.commit()

    payload = {
        "account_id": str(checking.id),
        "description": "Should fail",
        "total_amount": "1000.00",
        "num_installments": 3,
        "purchase_date": "2026-01-01",
    }
    response = await client.post(
        "/api/transactions/installments",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 400
