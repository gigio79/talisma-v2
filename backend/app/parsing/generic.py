from __future__ import annotations

import re
from decimal import Decimal

from app.parsing.base import BaseParser, ParsedTransaction


def _parse_valor(raw: str) -> Decimal:
    cleaned = raw.replace(".", "").replace(",", ".")
    return Decimal(cleaned)


# Generic Pix patterns (no app prefix):
#   "Pix recebido de João Silva no valor de R$ 50,00"
#   "Pix recebido de Maria Santos no valor de R$ 100,00"
#   "Pix recebidoVoce recebeu um Pix de Giovanni Bispo..."
#   "Você recebeu um Pix de Giovanni Bispo..."

_PIX_GENERIC = re.compile(
    r"(?:Vo[cç]e\s+)?recebeu(?:u)?\s+um\s+Pix\s+de\s+"
    r"(.+?)"
    r"(?:\s+CPF\s+[\d.*\-\s]+)?"
    r"\s*no\s+valor\s+de\s+R\$\s*([\d.,]+)",
    re.IGNORECASE | re.DOTALL,
)

_PIX_GENERIC_SIMPLE = re.compile(
    r"Pix\s+recebido\s+de\s+(.+?)\s+no\s+valor\s+de\s+R\$\s*([\d.,]+)",
    re.IGNORECASE,
)

_TRANSFERENCIA = re.compile(
    r"[Tt]ransfer[eê]ncia\s+recebida\s+de\s+(.+?)\s*[-–]\s*R\$\s*([\d.,]+)",
    re.IGNORECASE,
)

_COMPRA_CARTAO = re.compile(
    r"Compra\s+aprovada\s+no\s+cart[aã]o\s+final\s+(\d+)\s*[-–]\s*R\$\s*([\d.,]+)\s*[-–]\s*(.+?)(?:\.|$)",
    re.IGNORECASE,
)

_NOTIFICACAO_BANCARIA = re.compile(
    r"Notifica[cç][aã]o\s+banc[aá]ria:\s*R\$\s*([\d.,]+)\s*[-–]\s*(.+?)(?:\.|$)",
    re.IGNORECASE,
)


class GenericParser(BaseParser):
    """Fallback parser for notifications without a known app prefix."""

    app_name = "Desconhecido"

    def matches(self, text: str) -> bool:
        # Generic always matches — it's the fallback
        return True

    def parse(self, text: str, sender: str = "", app_hint: str = "") -> ParsedTransaction | None:
        banco = app_hint or self.app_name
        # Pix recebido (full pattern)
        m = _PIX_GENERIC.search(text)
        if m:
            nome = m.group(1).strip().rstrip(" -")
            valor = _parse_valor(m.group(2))
            return ParsedTransaction(
                banco_app=banco,
                tipo="credit",
                valor=valor,
                origem_destino=nome,
                descricao=f"Pix recebido de {nome} via {banco} — R$ {valor}",
                notificacao_original=text,
            )

        # Simple "Pix recebido de X no valor de R$ Y"
        m = _PIX_GENERIC_SIMPLE.search(text)
        if m:
            nome = m.group(1).strip()
            valor = _parse_valor(m.group(2))
            return ParsedTransaction(
                banco_app=banco,
                tipo="credit",
                valor=valor,
                origem_destino=nome,
                descricao=f"Pix recebido de {nome} via {banco} — R$ {valor}",
                notificacao_original=text,
            )

        # Transferência recebida
        m = _TRANSFERENCIA.search(text)
        if m:
            nome = m.group(1).strip()
            valor = _parse_valor(m.group(2))
            return ParsedTransaction(
                banco_app=banco,
                tipo="credit",
                valor=valor,
                origem_destino=nome,
                descricao=f"Transferência recebida de {nome} via {banco} — R$ {valor}",
                notificacao_original=text,
            )

        # Compra aprovada no cartão
        m = _COMPRA_CARTAO.search(text)
        if m:
            valor = _parse_valor(m.group(2))
            estabelecimento = m.group(3).strip()
            return ParsedTransaction(
                banco_app=banco,
                tipo="debit",
                valor=valor,
                origem_destino=estabelecimento,
                descricao=f"Compra em {estabelecimento} via {banco} — R$ {valor}",
                notificacao_original=text,
            )

        # Notificação bancária genérica
        m = _NOTIFICACAO_BANCARIA.search(text)
        if m:
            valor = _parse_valor(m.group(1))
            descricao = m.group(2).strip()
            return ParsedTransaction(
                banco_app=banco,
                tipo="debit",
                valor=valor,
                origem_destino=descricao,
                descricao=f"Notificação bancária: {descricao} via {banco} — R$ {valor}",
                notificacao_original=text,
            )

        return None
