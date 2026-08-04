from __future__ import annotations

import re

from app.parsing.base import (
    CREDITO,
    DEBITO,
    PIX_ENVIADO,
    PIX_RECEBIDO,
    BaseParser,
    ParsedTransaction,
    _parse_valor,
    make_transaction,
)

# Patterns observed in Neon notifications:
#   "Neon • agoraPix enviadoVoce enviou um Pix no valor de R$ 30,00."
#   "Neon • agoraPix recebidoVocê recebeu um Pix de Giovanni Bispo..."
#   "Você recebeu um Pix de Giovanni Bispo..."  (no app prefix)
#   "Neon • agoraCompra aprovadaCompra de R$ 40,00 em MERCADO LIVRE."

_PIX_ENVIADO = re.compile(
    r"(?:Neon\s*[•·]\s*agora)?\s*(?:Vo[cç][eéê]\s+)?enviou\s+um\s+Pix"
    r"(?:\s+(?:no\s+valor\s+de|de)\s+R\$\s*([\d.,]+))?",
    re.IGNORECASE | re.DOTALL,
)

_PIX_RECEBIDO = re.compile(
    r"(?:Neon\s*[•·]\s*agora)?"
    r"\s*(?:Vo[cç][eéê]\s+)?recebeu\s+um\s+Pix\s+de\s+"
    r"(.+?)"
    r"(?:\s+CPF\s+[\d.*\-\s]+)?"
    r"(?:\s*no\s+valor\s+de|\s+de)\s+R\$\s*([\d.,]+)",
    re.IGNORECASE | re.DOTALL,
)

# "Compra aprovadaCompra de R$ 40,00 em MERCADO LIVRE." — débito by default,
# crédito when the text says "no crédito"/"no cartão de crédito".
_COMPRA = re.compile(
    r"(?:Neon\s*[•·]\s*agora)?"
    r"\s*Compra\s*(?:aprovada\s*)?de\s+R\$\s*([\d.,]+)\s+em\s+"
    r"(.+?)(?:\.|$)",
    re.IGNORECASE | re.DOTALL,
)


class NeonParser(BaseParser):
    app_name = "Neon"
    aliases = ("Banco Neon",)

    def matches(self, text: str) -> bool:
        lower = text.lower()
        return "neon" in lower

    def parse(self, text: str, sender: str = "", app_hint: str = "") -> ParsedTransaction | None:
        banco = app_hint or self.app_name

        # Pix enviado (débito) — establishment not informed by Neon.
        m = _PIX_ENVIADO.search(text)
        if m:
            valor = _parse_valor(m.group(1)) if m.group(1) else None
            if valor is None:
                return None
            return make_transaction(
                banco_app=banco,
                movement_type=PIX_ENVIADO,
                valor=valor,
                origem_destino="Não informado",
                notificacao_original=text,
            )

        # Pix recebido (crédito)
        m = _PIX_RECEBIDO.search(text)
        if m:
            nome = m.group(1).strip().rstrip(" -")
            valor = _parse_valor(m.group(2))
            if valor is None:
                return None
            return make_transaction(
                banco_app=banco,
                movement_type=PIX_RECEBIDO,
                valor=valor,
                origem_destino=nome,
                notificacao_original=text,
            )

        # Compra aprovada (débito/crédito)
        m = _COMPRA.search(text)
        if m:
            valor = _parse_valor(m.group(1))
            if valor is None:
                return None
            estabelecimento = m.group(2).strip().rstrip(" -")
            movement = CREDITO if re.search(r"cr[eé]dito", text, re.IGNORECASE) else DEBITO
            return make_transaction(
                banco_app=banco,
                movement_type=movement,
                valor=valor,
                origem_destino=estabelecimento,
                notificacao_original=text,
            )

        return None
