from __future__ import annotations

import re
from decimal import Decimal

from app.parsing.base import BaseParser, ParsedTransaction


def _parse_valor(raw: str) -> Decimal:
    cleaned = raw.replace(".", "").replace(",", ".")
    return Decimal(cleaned)


# PicPay patterns:
#   "PicPay • agoraGIOVANNI BISPO... enviou um Pix para vocêVocê recebeu um Pix de R$0,65."
#   "PicPay • agoraVocê recebeu R$ 10,00 de Maria Silva"

_PIX_ENVIADO_PICPAY = re.compile(
    r"PicPay\s*[•·]\s*agora(.+?)\s+enviou\s+um\s+Pix\s+para\s+voc[eê]"
    r".*?R\$\s*([\d.,]+)",
    re.IGNORECASE | re.DOTALL,
)

_PIX_RECEBIDO_PICPAY = re.compile(
    r"PicPay\s*[•·]\s*agora"
    r"(?:Vo[cç]e\s+)?recebeu\s+um\s+Pix\s+de\s+R\$\s*([\d.,]+)",
    re.IGNORECASE | re.DOTALL,
)

_PIX_RECEBIDO_DE = re.compile(
    r"(?:Vo[cç]e\s+)?recebeu[s]?\s+R\$\s*([\d.,]+)\s+de\s+(.+?)(?:\.|$)",
    re.IGNORECASE | re.DOTALL,
)


class PicPayParser(BaseParser):
    app_name = "PicPay"

    def matches(self, text: str) -> bool:
        return "picpay" in text.lower()

    def parse(self, text: str, sender: str = "", app_hint: str = "") -> ParsedTransaction | None:
        banco = app_hint or self.app_name
        # Outgoing Pix
        m = _PIX_ENVIADO_PICPAY.search(text)
        if m:
            nome = m.group(1).strip()
            valor = _parse_valor(m.group(2))
            return ParsedTransaction(
                banco_app=banco,
                tipo="debit",
                valor=valor,
                origem_destino=nome,
                descricao=f"Pix enviado para {nome} via {banco} — R$ {valor}",
                notificacao_original=text,
            )

        # Incoming Pix with explicit value
        m = _PIX_RECEBIDO_PICPAY.search(text)
        if m:
            valor = _parse_valor(m.group(1))
            return ParsedTransaction(
                banco_app=banco,
                tipo="credit",
                valor=valor,
                origem_destino="PicPay",
                descricao=f"Pix recebido via {banco} — R$ {valor}",
                notificacao_original=text,
            )

        # "recebeu R$ X de Nome"
        m = _PIX_RECEBIDO_DE.search(text)
        if m:
            valor = _parse_valor(m.group(1))
            nome = m.group(2).strip().rstrip(".")
            return ParsedTransaction(
                banco_app=banco,
                tipo="credit",
                valor=valor,
                origem_destino=nome,
                descricao=f"Pix recebido de {nome} via {banco} — R$ {valor}",
                notificacao_original=text,
            )

        return None
