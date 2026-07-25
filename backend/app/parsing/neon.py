from __future__ import annotations

import re
from decimal import Decimal

from app.parsing.base import BaseParser, ParsedTransaction

# Patterns observed in Neon notifications:
#   "Neon • agoraPix enviadoVoce enviou um Pix no valor de R$ 30,00."
#   "Neon • agoraPix recebidoVocê recebeu um Pix de Giovanni Bispo..."
#   "Você recebeu um Pix de Giovanni Bispo..."  (no app prefix)

_PIX_ENViado = re.compile(
    r"(?:Neon\s*[•·]\s*agora)?(?:Vo[cç]e\s+)?enviou\s+um\s+Pix"
    r"(?:\s+de\s+R\$\s*([\d.,]+))?"
    r".*?no\s+valor\s+de\s+R\$\s*([\d.,]+)",
    re.IGNORECASE | re.DOTALL,
)

_PIX_RECEBIDO = re.compile(
    r"(?:Neon\s*[•·]\s*agora)?"
    r"(?:Vo[cç]e\s+)?recebeu\s+um\s+Pix\s+de\s+"
    r"(.+?)"
    r"(?:\s+CPF\s+[\d.*\-\s]+)?"
    r"\s*no\s+valor\s+de\s+R\$\s*([\d.,]+)",
    re.IGNORECASE | re.DOTALL,
)

_VALOR = re.compile(r"R\$\s*([\d.,]+)")


def _parse_valor(raw: str) -> Decimal:
    """Convert a Brazilian-formatted currency string to Decimal."""
    cleaned = raw.replace(".", "").replace(",", ".")
    return Decimal(cleaned)


class NeonParser(BaseParser):
    app_name = "Neon"

    def matches(self, text: str) -> bool:
        lower = text.lower()
        return "neon" in lower

    def parse(self, text: str, sender: str = "", app_hint: str = "") -> ParsedTransaction | None:
        banco = app_hint or self.app_name
        # Pix enviado (débito)
        m = _PIX_ENViado.search(text)
        if m:
            valor = _parse_valor(m.group(2))
            return ParsedTransaction(
                banco_app=banco,
                tipo="debit",
                valor=valor,
                origem_destino="Pix enviado",
                descricao=f"Pix enviado via {banco} — R$ {valor}",
                notificacao_original=text,
            )

        # Pix recebido (crédito)
        m = _PIX_RECEBIDO.search(text)
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

        return None
