from __future__ import annotations

import re
from decimal import Decimal

from app.parsing.base import BaseParser, ParsedTransaction


def _parse_valor(raw: str) -> Decimal:
    cleaned = raw.replace(".", "").replace(",", ".")
    return Decimal(cleaned)


# Nubank credit purchase patterns:
#   "Compra de R$ 50,50 APROVADA em LOJAS AMERICANAS 569 para o cartão com final 4251."
#   "Compra de R$ 120,00 aprovada em PADARIA BOA 123 no cartão final 1234"

_COMPRA_CREDITO = re.compile(
    r"[Cc]ompra\s+de\s+R\$\s*([\d.,]+)\s+"
    r"(?:APROVADA|aprovada)\s+em\s+"
    r"(.+?)\s+"
    r"(?:para\s+o\s+cart[aã]o\s+com\s+final|no\s+cart[aã]o\s+final)\s+"
    r"(\d{4})",
    re.IGNORECASE,
)


class NubankParser(BaseParser):
    app_name = "Nubank"

    def matches(self, text: str) -> bool:
        lower = text.lower()
        return "nubank" in lower or ("compra" in lower and "aprovada" in lower and "cartão" in lower)

    def parse(self, text: str, sender: str = "", app_hint: str = "") -> ParsedTransaction | None:
        banco = app_hint or self.app_name
        m = _COMPRA_CREDITO.search(text)
        if m:
            valor = _parse_valor(m.group(1))
            estabelecimento = m.group(2).strip()
            return ParsedTransaction(
                banco_app=banco,
                tipo="debit",
                valor=valor,
                origem_destino=estabelecimento,
                descricao=f"Compra em {estabelecimento} via {banco} — R$ {valor}",
                notificacao_original=text,
            )

        return None
