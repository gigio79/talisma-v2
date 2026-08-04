from __future__ import annotations

import re

from app.parsing.base import (
    CREDITO,
    BaseParser,
    ParsedTransaction,
    _parse_valor,
    make_transaction,
)

# Google Wallet / Carteira do Google card notification:
#   "IFOOD · 1h\nR$ 39,90 com Cartão Mercado Pago · 2109"
# Establishment comes from the title ("IFOOD"), card last-4 from the body
# ("Cartão Mercado Pago · 2109").

_TITLE_ESTABELECIMENTO = re.compile(r"^(.+?)\s*·", re.MULTILINE)

_VALOR = re.compile(r"R\$\s*([\d.,]+)", re.IGNORECASE)

_CARTAO_FINAL = re.compile(r"Cart[aã]o\s+[\w\s]*·\s*(\d{4})(?:\s|$)", re.IGNORECASE)


class GoogleWalletParser(BaseParser):
    app_name = "Google Wallet"
    aliases = ("Google", "Google Pay", "Carteira do Google")

    def matches(self, text: str) -> bool:
        lower = text.lower()
        return "google" in lower or "carteira do google" in lower

    def parse(self, text: str, sender: str = "", app_hint: str = "") -> ParsedTransaction | None:
        banco = app_hint or self.app_name

        m_valor = _VALOR.search(text)
        if not m_valor:
            return None
        valor = _parse_valor(m_valor.group(1))
        if valor is None:
            return None

        m_estab = _TITLE_ESTABELECIMENTO.search(text)
        estabelecimento = m_estab.group(1).strip() if m_estab else ""
        if not estabelecimento and "\n" in text:
            estabelecimento = text.splitlines()[0].strip()

        m_cartao = _CARTAO_FINAL.search(text)
        cartao_final = m_cartao.group(1) if m_cartao else None

        return make_transaction(
            banco_app=banco,
            movement_type=CREDITO,
            valor=valor,
            origem_destino=estabelecimento,
            notificacao_original=text,
            cartao_final=cartao_final,
        )
