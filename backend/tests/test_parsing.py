"""Unit tests for the MacroDroid notification parsers.

Each test exercises the spec examples from the MacroDroid parser spec:
field extraction (valor, estabelecimento, tipo), standardized description,
and the fallback rules (no value → None; value but no match → minimal
transaction flagged for review).
"""
from decimal import Decimal

from app.parsing import parse_notification

# ---------------------------------------------------------------------------
# Pix recebido / enviado / compra
# ---------------------------------------------------------------------------


def test_neon_pix_enviado():
    result = parse_notification(
        "Neon • agoraPix enviadoVoce enviou um Pix no valor de R$ 30,00.",
        app="Neon",
    )
    assert result is not None
    assert result.banco_app == "Neon"
    assert result.movement_type == "pix_enviado"
    assert result.tipo == "debit"
    assert result.valor == Decimal("30.00")
    assert result.origem_destino == "Não informado"
    assert result.descricao == "Pix enviado para Não informado via Neon"
    assert result.precisa_revisao is False


def test_neon_pix_recebido():
    result = parse_notification(
        "Neon • agoraPix recebidoVocê recebeu um Pix de Giovanni Bispo no valor de R$ 100,00.",
        app="Neon",
    )
    assert result is not None
    assert result.movement_type == "pix_recebido"
    assert result.tipo == "credit"
    assert result.valor == Decimal("100.00")
    assert result.origem_destino == "Giovanni Bispo"
    assert result.descricao == "Pix recebido de Giovanni Bispo via Neon"


def test_neon_compra_debito():
    result = parse_notification(
        "Neon • agoraCompra aprovadaCompra de R$ 40,00 em MERCADO LIVRE.",
        app="Neon",
    )
    assert result is not None
    assert result.movement_type == "debito"
    assert result.tipo == "debit"
    assert result.valor == Decimal("40.00")
    assert result.origem_destino == "MERCADO LIVRE"
    assert result.descricao == "Compra em MERCADO LIVRE via Neon"


def test_picpay_pix_recebido_com_titulo():
    result = parse_notification(
        "PicPay • agoraGIOVANNI BISPO teste enviou um Pix para vocêVocê recebeu um Pix de R$0,65.",
        app="PicPay",
    )
    assert result is not None
    assert result.movement_type == "pix_recebido"
    assert result.tipo == "credit"
    assert result.valor == Decimal("0.65")
    assert result.origem_destino == "GIOVANNI BISPO teste"
    assert result.descricao == "Pix recebido de GIOVANNI BISPO teste via PicPay"


def test_picpay_pix_recebido_com_nome_no_corpo():
    result = parse_notification(
        "PicPay • agoraVocê recebeu R$ 10,00 de Maria Silva",
        app="PicPay",
    )
    assert result is not None
    assert result.movement_type == "pix_recebido"
    assert result.tipo == "credit"
    assert result.valor == Decimal("10.00")
    assert result.origem_destino == "Maria Silva"


def test_picpay_pix_recebido_sem_prefixo():
    result = parse_notification(
        "Você recebeu um Pix de R$ 0,01. Toque para visualizar o pagamento",
        app="PicPay",
    )
    assert result is not None
    assert result.movement_type == "pix_recebido"
    assert result.tipo == "credit"
    assert result.valor == Decimal("0.01")
    assert result.origem_destino == "Não informado"
    assert result.descricao == "Pix recebido de Não informado via PicPay"
    assert result.precisa_revisao is False


def test_picpay_pix_enviado_tela():
    result = parse_notification(
        "Pix enviado para Debora Ribeiro de Campos no valor de R$ 0,01",
        app="PicPay",
    )
    assert result is not None
    assert result.movement_type == "pix_enviado"
    assert result.tipo == "debit"
    assert result.valor == Decimal("0.01")
    assert result.origem_destino == "Debora Ribeiro de Campos"
    assert result.descricao == "Pix enviado para Debora Ribeiro de Campos via PicPay"
    assert result.precisa_revisao is False


def test_picpay_compra_aprovada():
    result = parse_notification(
        "Compra de R$ 38,00 em Cido Motos foi APROVADA.",
        app="PicPay",
    )
    assert result is not None
    assert result.movement_type == "debito"
    assert result.tipo == "debit"
    assert result.valor == Decimal("38.00")
    assert result.origem_destino == "Cido Motos"
    assert result.descricao == "Compra em Cido Motos via PicPay"
    assert result.precisa_revisao is False


def test_nubank_compra_credito_com_cartao_final():
    result = parse_notification(
        "Compra de R$ 19,90 APROVADA em AMAZON BRASIL 1112 para o cartão com final 4251.",
        app="Nubank",
    )
    assert result is not None
    assert result.movement_type == "credito"
    assert result.tipo == "credit"
    assert result.valor == Decimal("19.90")
    assert result.origem_destino == "AMAZON BRASIL 1112"
    assert result.cartao_final == "4251"
    assert result.descricao == "Compra em AMAZON BRASIL 1112 via Nubank"


def test_nubank_pix_enviado():
    result = parse_notification(
        "Você fez um Pix no valor de R$ 273,82 para CENCOSUD BRASIL ATACADO LTDA.",
        app="Nubank",
    )
    assert result is not None
    assert result.movement_type == "pix_enviado"
    assert result.tipo == "debit"
    assert result.valor == Decimal("273.82")
    assert result.origem_destino == "CENCOSUD BRASIL ATACADO LTDA"


def test_mercado_pago_pagamento():
    result = parse_notification(
        "Mercado Pago • agoraVocê pagou PADARIA DO ZÉ. Debitamos R$ 40,00 da sua conta.",
        app="Mercado Pago",
    )
    assert result is not None
    assert result.movement_type == "debito"
    assert result.tipo == "debit"
    assert result.valor == Decimal("40.00")
    assert result.origem_destino == "PADARIA DO ZÉ"
    assert result.descricao == "Compra em PADARIA DO ZÉ via Mercado Pago"


def test_infinitepay_pix_enviado():
    result = parse_notification(
        "InfinitePay — Pix enviado\nPix enviado com sucesso! ✅\n"
        "Você fez um Pix no valor de R$ 273,82 para CENCOSUD BRASIL ATACADO LTDA.",
        app="InfinitePay",
    )
    assert result is not None
    assert result.movement_type == "pix_enviado"
    assert result.tipo == "debit"
    assert result.valor == Decimal("273.82")
    assert result.origem_destino == "CENCOSUD BRASIL ATACADO LTDA"


def test_google_wallet_compra():
    result = parse_notification(
        "IFOOD · 1h\nR$ 39,90 com Cartão Mercado Pago · 2109",
        app="Google Wallet",
    )
    assert result is not None
    assert result.movement_type == "credito"
    assert result.tipo == "credit"
    assert result.valor == Decimal("39.90")
    assert result.origem_destino == "IFOOD"
    assert result.cartao_final == "2109"
    assert result.descricao == "Compra em IFOOD via Google Wallet"


def test_google_wallet_aliase_carteira_do_google():
    result = parse_notification(
        "IFOOD · 1h\nR$ 39,90 com Cartão Mercado Pago · 2109",
        app="Carteira do Google",
    )
    assert result is not None
    assert result.origem_destino == "IFOOD"
    assert result.cartao_final == "2109"


def test_parser_escolhido_pelo_hint_mesmo_sem_palavra_chave():
    # The Google Wallet text has no "google" keyword; the app hint drives it.
    result = parse_notification(
        "IFOOD · 1h\nR$ 39,90 com Cartão Mercado Pago · 2109",
        app="Google Wallet",
    )
    assert result is not None


# ---------------------------------------------------------------------------
# Fallbacks (spec section 4)
# ---------------------------------------------------------------------------


def test_sem_valor_retorna_none():
    result = parse_notification(
        "Esta notificação não tem valor nenhum aqui",
        app="Banco X",
    )
    assert result is None


def test_valor_sem_match_estruturado_gera_transacao_minima_para_revisao():
    result = parse_notification(
        "Notificação desconhecida dizendo alguma coisa sobre R$ 12,34 qualquer",
        app="Banco X",
    )
    assert result is not None
    assert result.precisa_revisao is True
    assert result.valor == Decimal("12.34")
    assert result.origem_destino == "Estabelecimento não identificado"
    assert result.descricao == "Transação pendente de revisão - Banco X"
    assert result.tipo == "debit"


def test_sem_estabelecimento_com_valor_gera_placeholder():
    # A pix_recebido notification where the establishment extraction is
    # impossible but a value exists must fall back to the placeholder.
    result = parse_notification(
        "Você recebeu um Pix de R$ 5,00",
        app="Banco Y",
    )
    assert result is not None
    assert result.valor == Decimal("5.00")
    assert result.origem_destino == "Estabelecimento não identificado"
    assert result.precisa_revisao is True


def test_valor_formatado_com_milhar():
    result = parse_notification(
        "Você fez um Pix no valor de R$ 1.234,56 para NOME DA EMPRESA",
        app="Nubank",
    )
    assert result is not None
    assert result.valor == Decimal("1234.56")
