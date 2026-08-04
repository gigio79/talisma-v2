"""Integration tests for the MacroDroid webhook endpoint.

Covers the spec fallback rules end-to-end:
- Valid notifications create a transaction with the parsed fields.
- Missing value → 422 and NO transaction is created (log + admin alert).
- Value present but no structured match → minimal transaction flagged for
  manual review.
- Bearer-token auth when a secret is configured.
"""
import uuid
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import select

from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import WorkspaceMember

NOTES_FIXO = "Criado automaticamente pelo MacroDroid:"


@pytest_asyncio.fixture
async def webhook_settings(monkeypatch):
    """Pin deterministic (authless) webhook settings for the test process."""
    from app.core.config import Settings

    settings = Settings(macrodroid_webhook_secret="", macrodroid_workspace_id="")
    monkeypatch.setattr("app.api.webhooks.get_settings", lambda: settings)
    return settings


def _payload(
    text: str,
    app: str = "PicPay",
    sender: str = "Giovanni",
    workspace_id: str | None = None,
    estabelecimento: str | None = None,
    categoria: str | None = None,
):
    data = {"text": text, "sender": sender, "app": app}
    if workspace_id:
        data["workspace_id"] = workspace_id
    if estabelecimento is not None:
        data["estabelecimento"] = estabelecimento
    if categoria is not None:
        data["categoria"] = categoria
    return data


async def _last_transaction(session, description: str) -> Transaction:
    result = await session.execute(
        select(Transaction).where(Transaction.description == description).order_by(Transaction.created_at.desc())
    )
    return result.scalar_one()


async def test_webhook_cria_transacao_picpay(client, session, test_user, test_workspace, webhook_settings):
    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "PicPay • agoraGIOVANNI BISPO teste enviou um Pix para vocêVocê recebeu um Pix de R$0,65.",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["amount"] == "0.65"
    assert body["type"] == "credit"
    assert body["movement_type"] == "pix_recebido"
    assert body["needs_review"] is False

    tx = await _last_transaction(session, body["description"])
    assert tx.description == "Pix recebido de GIOVANNI BISPO teste via PicPay"
    assert tx.amount == Decimal("0.65")
    assert tx.type == "credit"
    assert tx.movement_type == "pix_recebido"
    assert tx.source_app == "PicPay"
    assert tx.sender == "Giovanni"
    assert tx.notes.startswith(NOTES_FIXO)
    assert tx.payee == "GIOVANNI BISPO teste"
    assert tx.card_last4 is None
    assert tx.needs_review is False
    assert tx.raw_data["notificacao_original"] == _payload(
        "PicPay • agoraGIOVANNI BISPO teste enviou um Pix para vocêVocê recebeu um Pix de R$0,65.",
    )["text"]


async def test_webhook_compra_credito_salva_cartao_final(client, session, test_user, test_workspace, webhook_settings):
    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Compra de R$ 19,90 APROVADA em AMAZON BRASIL 1112 para o cartão com final 4251.",
            app="Nubank",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    tx = await _last_transaction(session, body["description"])
    assert tx.movement_type == "credito"
    assert tx.type == "credit"
    assert tx.card_last4 == "4251"
    assert tx.needs_review is False


async def test_webhook_sem_valor_nao_cria_transacao(client, session, test_user, test_workspace, webhook_settings):
    before = (await session.execute(select(Transaction))).scalars().all()
    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Esta notificação não tem valor nenhum aqui",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 422, response.text
    after = (await session.execute(select(Transaction))).scalars().all()
    assert len(after) == len(before)


async def test_webhook_valor_sem_match_cria_transacao_para_revisao(
    client, session, test_user, test_workspace, webhook_settings
):
    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Notificação desconhecida dizendo alguma coisa sobre R$ 12,34 qualquer",
            app="Banco X",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["needs_review"] is True
    assert body["movement_type"] is None

    tx = await _last_transaction(session, body["description"])
    assert tx.description == "Transação pendente de revisão - Banco X (Giovanni)"
    assert tx.amount == Decimal("12.34")
    assert tx.needs_review is True
    assert tx.payee is None  # placeholder payee not persisted


async def _add_member_user(session, workspace_id, email: str, display_name: str) -> User:
    import bcrypt as _bcrypt

    hashed = _bcrypt.hashpw(b"testpass123", _bcrypt.gensalt()).decode()
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hashed,
        is_active=True,
        is_superuser=False,
        is_verified=True,
        preferences={
            "language": "pt-BR",
            "date_format": "DD/MM/YYYY",
            "timezone": "America/Sao_Paulo",
            "currency_display": "BRL",
            "display_name": display_name,
        },
    )
    session.add(user)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            user_id=user.id,
            role="editor",
        )
    )
    await session.commit()
    await session.refresh(user)
    return user


async def test_webhook_rota_para_conta_do_titular_do_sender(
    client, session, test_user, test_workspace, webhook_settings
):
    debora = await _add_member_user(session, test_workspace.id, "debora@example.com", "Débora")
    debora_picpay = await _create_account(session, test_workspace.id, debora.id, "PicPay", "checking")
    await _create_account(session, test_workspace.id, test_user.id, "PicPay", "checking")

    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Notificação desconhecida dizendo alguma coisa sobre R$ 12,34 qualquer",
            app="PicPay",
            sender="Débora",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    tx = await _last_transaction(session, response.json()["description"])
    assert tx.sender == "Débora"
    assert tx.needs_review is True
    assert tx.description == "Transação pendente de revisão - PicPay (Débora)"
    assert tx.account_id == debora_picpay.id
    assert tx.user_id == test_user.id  # transaction stays on the workspace owner


async def test_webhook_sender_sem_match_cai_na_conta_do_dono(
    client, session, test_user, test_workspace, webhook_settings
):
    owner_picpay = await _create_account(session, test_workspace.id, test_user.id, "PicPay", "checking")

    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Notificação desconhecida dizendo alguma coisa sobre R$ 12,34 qualquer",
            app="PicPay",
            sender="Desconhecido",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    tx = await _last_transaction(session, response.json()["description"])
    assert tx.sender == "Desconhecido"
    assert tx.account_id == owner_picpay.id
    assert tx.user_id == test_user.id


async def test_webhook_auth_exigida_quando_secret_configurado(client, session, test_user, test_workspace, monkeypatch):
    from app.core.config import Settings

    settings = Settings(macrodroid_webhook_secret="top-secret", macrodroid_workspace_id="")
    monkeypatch.setattr("app.api.webhooks.get_settings", lambda: settings)

    data = _payload(
        "PicPay • agoraVocê recebeu R$ 10,00 de Maria Silva",
        workspace_id=str(test_workspace.id),
    )

    # Missing header
    response = await client.post("/api/webhooks/macrodroid", json=data)
    assert response.status_code == 401

    # Wrong token
    response = await client.post(
        "/api/webhooks/macrodroid",
        json=data,
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 403

    # Correct token
    response = await client.post(
        "/api/webhooks/macrodroid",
        json=data,
        headers={"Authorization": "Bearer top-secret"},
    )
    assert response.status_code == 200, response.text


async def test_webhook_conta_criada_por_app(client, session, test_user, test_workspace, webhook_settings):
    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "PicPay • agoraVocê recebeu R$ 10,00 de Maria Silva",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text

    result = await session.execute(
        select(Account).where(Account.workspace_id == test_workspace.id, Account.name == "PicPay")
    )
    account = result.scalar_one()
    assert account.type == "checking"
    assert account.currency == "BRL"
    assert account.user_id == test_user.id


async def test_webhook_workspace_inexistente_retorna_404(client, session, test_user, test_workspace, webhook_settings):
    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "PicPay • agoraVocê recebeu R$ 10,00 de Maria Silva",
            workspace_id=str(uuid.uuid4()),
        ),
    )
    assert response.status_code == 404


async def _create_account(
    session,
    workspace_id,
    user_id,
    name: str,
    acc_type: str,
    masked_number: str | None = None,
) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        name=name,
        type=acc_type,
        currency="BRL",
        balance=Decimal("0.00"),
        masked_number=masked_number,
    )
    session.add(account)
    await session.commit()
    return account


async def test_webhook_pix_rota_para_conta_corrente_do_app(
    client, session, test_user, test_workspace, webhook_settings
):
    checking = await _create_account(session, test_workspace.id, test_user.id, "Neon", "checking")
    await _create_account(session, test_workspace.id, test_user.id, "Neon", "credit_card")

    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Neon • agoraPix recebidoVocê recebeu um Pix de Giovanni Bispo no valor de R$ 0,01",
            app="Neon",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    tx = await _last_transaction(session, response.json()["description"])
    assert tx.movement_type == "pix_recebido"
    assert tx.account_id == checking.id


async def test_webhook_credito_rota_para_cartao_pelo_card_last4(
    client, session, test_user, test_workspace, webhook_settings
):
    cartao = await _create_account(
        session, test_workspace.id, test_user.id, "Nubank", "credit_card", masked_number="4251"
    )
    await _create_account(session, test_workspace.id, test_user.id, "Nubank", "checking")

    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Compra de R$ 19,90 APROVADA em AMAZON BRASIL 1112 para o cartão com final 4251.",
            app="Nubank",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    tx = await _last_transaction(session, response.json()["description"])
    assert tx.movement_type == "credito"
    assert tx.card_last4 == "4251"
    assert tx.account_id == cartao.id


async def test_webhook_credito_sem_cartao_casado_rota_para_cartao_do_app(
    client, session, test_user, test_workspace, webhook_settings
):
    cartao = await _create_account(session, test_workspace.id, test_user.id, "Nubank", "credit_card")

    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Compra de R$ 19,90 APROVADA em AMAZON BRASIL 1112 para o cartão com final 9999.",
            app="Nubank",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    tx = await _last_transaction(session, response.json()["description"])
    assert tx.movement_type == "credito"
    assert tx.account_id == cartao.id


async def test_webhook_pix_sem_conta_corrente_usa_primeira_conta_do_app(
    client, session, test_user, test_workspace, webhook_settings
):
    cartao = await _create_account(session, test_workspace.id, test_user.id, "Neon", "credit_card")

    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Neon • agoraPix recebidoVocê recebeu um Pix de Giovanni Bispo no valor de R$ 0,01",
            app="Neon",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    tx = await _last_transaction(session, response.json()["description"])
    assert tx.movement_type == "pix_recebido"
    assert tx.account_id == cartao.id


async def test_webhook_estabelecimento_preenchido_pelo_usuario_priorizado(
    client, session, test_user, test_workspace, webhook_settings
):
    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Pix recebido\nVocê recebeu um Pix de Giovanni Bispo Dos Reis Silva CPF ***.727.668-** "
            "no valor de R$ 0,01.",
            app="Neon",
            estabelecimento="pão de açúcar",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["description"] == "Pix recebido de pão de açúcar via Neon"
    assert body["payee"] == "pão de açúcar"
    assert body["needs_review"] is False

    tx = await _last_transaction(session, body["description"])
    assert tx.description == "Pix recebido de pão de açúcar via Neon"
    assert tx.payee == "pão de açúcar"
    assert tx.payee_id is not None  # establishment becomes a Payee entity
    assert tx.amount == Decimal("0.01")
    assert tx.type == "credit"
    assert tx.movement_type == "pix_recebido"
    assert tx.needs_review is False
    assert tx.raw_data["estabelecimento"] == "pão de açúcar"
    assert tx.raw_data["categoria"] is None


async def test_webhook_sem_estabelecimento_usa_nome_do_parser(
    client, session, test_user, test_workspace, webhook_settings
):
    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Pix recebido\nVocê recebeu um Pix de Giovanni Bispo Dos Reis Silva no valor de R$ 0,01.",
            app="Neon",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["description"] == "Pix recebido de Giovanni Bispo Dos Reis Silva via Neon"
    tx = await _last_transaction(session, body["description"])
    assert tx.payee == "Giovanni Bispo Dos Reis Silva"
    assert tx.needs_review is False


async def test_webhook_categoria_existente_ligada_case_insensitive(
    client, session, test_user, test_workspace, webhook_settings
):
    from app.schemas.category import CategoryCreate
    from app.services.category_service import create_category

    categoria = await create_category(
        session,
        test_workspace.id,
        test_user.id,
        CategoryCreate(name="Padaria", icon="croissant", color="#F59E0B"),
    )

    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Pix recebido\nVocê recebeu um Pix de Giovanni Bispo no valor de R$ 0,01.",
            app="Neon",
            estabelecimento="pão de açúcar",
            categoria="padaria",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["category"] == "Padaria"
    tx = await _last_transaction(session, response.json()["description"])
    assert tx.category_id == categoria.id

    result = await session.execute(
        select(Category).where(Category.workspace_id == test_workspace.id)
    )
    assert len(result.scalars().all()) == 1  # no duplicate created


async def test_webhook_categoria_inexistente_auto_criada(
    client, session, test_user, test_workspace, webhook_settings
):
    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Pix recebido\nVocê recebeu um Pix de Giovanni Bispo no valor de R$ 0,01.",
            app="Neon",
            estabelecimento="pão de açúcar",
            categoria="padaria",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["category"] == "padaria"

    tx = await _last_transaction(session, response.json()["description"])
    assert tx.category_id is not None

    result = await session.execute(
        select(Category).where(Category.workspace_id == test_workspace.id)
    )
    cats = result.scalars().all()
    assert len(cats) == 1
    assert cats[0].name == "padaria"
    assert cats[0].is_system is False
    assert cats[0].group_id is None
    assert cats[0].id == tx.category_id


async def test_webhook_exemplo_completo_do_prompt(
    client, session, test_user, test_workspace, webhook_settings
):
    response = await client.post(
        "/api/webhooks/macrodroid",
        json=_payload(
            "Pix recebido\nVocê recebeu um Pix de Giovanni Bispo Dos Reis Silva CPF ***.727.668-** "
            "no valor de R$ 0,01.",
            app="Neon",
            estabelecimento="pão de açúcar",
            categoria="padaria",
            workspace_id=str(test_workspace.id),
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["description"] == "Pix recebido de pão de açúcar via Neon"
    assert body["amount"] == "0.01"
    assert body["category"] == "padaria"
    assert body["payee"] == "pão de açúcar"
    assert body["type"] == "credit"
    assert body["movement_type"] == "pix_recebido"
    assert body["needs_review"] is False

    tx = await _last_transaction(session, body["description"])
    assert tx.description == "Pix recebido de pão de açúcar via Neon"
    assert tx.amount == Decimal("0.01")
    assert tx.type == "credit"
    assert tx.movement_type == "pix_recebido"
    assert tx.needs_review is False
    assert tx.payee == "pão de açúcar"
    assert tx.payee_id is not None
    assert tx.category_id is not None
    result = await session.execute(
        select(Category).where(Category.id == tx.category_id)
    )
    assert result.scalar_one().name == "padaria"


async def test_webhook_categoria_repetida_nao_duplica(
    client, session, test_user, test_workspace, webhook_settings
):
    for _ in range(2):
        response = await client.post(
            "/api/webhooks/macrodroid",
            json=_payload(
                "Pix recebido\nVocê recebeu um Pix de Giovanni Bispo no valor de R$ 0,01.",
                app="Neon",
                categoria="padaria",
                workspace_id=str(test_workspace.id),
            ),
        )
        assert response.status_code == 200, response.text

    result = await session.execute(
        select(Category).where(Category.workspace_id == test_workspace.id)
    )
    assert len(result.scalars().all()) == 1

    txs = (
        await session.execute(
            select(Transaction).where(Transaction.workspace_id == test_workspace.id)
        )
    ).scalars().all()
    assert len(txs) == 2
    assert txs[0].category_id == txs[1].category_id
    assert txs[0].category_id is not None
