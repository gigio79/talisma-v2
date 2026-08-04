from __future__ import annotations

import re

from app.parsing.base import (
    PIX_ENVIADO,
    PIX_RECEBIDO,
    BaseParser,
    ParsedTransaction,
    _parse_valor,
    make_transaction,
)

# PicPay patterns:
#   "PicPay • agoraGIOVANNI BISPO... enviou um Pix para vocêVocê recebeu um Pix de R$0,65."
#   "PicPay • agoraVocê recebeu R$ 10,00 de Maria Silva"
#   "PicPay • agoraPix enviadoVocê enviou um Pix no valor de R$ 30,00 para NOME."

# Someone sent a Pix TO you (crédito). The sender name lives in the title,
# the value in the body.
_PIX_RECEBIDO_TITULO = re.compile(
    r"PicPay\s*[•·]\s*agora(.+?)\s+enviou\s+um\s+Pix\s+para\s+voc[eêê]"
    r".*?R\$\s*([\d.,]+)",
    re.IGNORECASE | re.DOTALL,
)

# "Você recebeu um Pix de R$X" without a sender name.
_PIX_RECEBIDO_VALOR = re.compile(
    r"PicPay\s*[•·]\s*agora"
    r"\s*(?:Vo[cç][eéê]\s+)?recebeu\s+um\s+Pix\s+de\s+R\$\s*([\d.,]+)",
    re.IGNORECASE | re.DOTALL,
)

# "Você recebeu R$ X de Nome"
_PIX_RECEBIDO_DE = re.compile(
    r"\s*(?:Vo[cç][eéê]\s+)?recebeu[s]?\s+R\$\s*([\d.,]+)\s+de\s+(.+?)(?:\.|$)",
    re.IGNORECASE | re.DOTALL,
)

# "Você enviou um Pix no valor de R$ X para NOME" (débito)
_PIX_ENVIADO = re.compile(
    r"PicPay\s*[•·]\s*agora"
    r"\s*(?:Vo[cç][eéê]\s+)?enviou\s+um\s+Pix\s+(?:no\s+valor\s+de\s+)?R\$\s*([\d.,]+)\s+para\s+"
    r"(.+?)(?:\.|$)",
    re.IGNORECASE | re.DOTALL,
)


class PicPayParser(BaseParser):
    app_name = "PicPay"

    def matches(self, text: str) -> bool:
        return "picpay" in text.lower()

    def parse(self, text: str, sender: str = "", app_hint: str = "") -> ParsedTransaction | None:
        banco = app_hint or self.app_name

        # Incoming Pix with the sender name in the title (crédito).
        m = _PIX_RECEBIDO_TITULO.search(text)
        if m:
            nome = m.group(1).strip()
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

        # Incoming Pix with explicit value but no name.
        m = _PIX_RECEBIDO_VALOR.search(text)
        if m:
            valor = _parse_valor(m.group(1))
            if valor is None:
                return None
            return make_transaction(
                banco_app=banco,
                movement_type=PIX_RECEBIDO,
                valor=valor,
                origem_destino="Não informado",
                notificacao_original=text,
            )

        # "recebeu R$ X de Nome"
        m = _PIX_RECEBIDO_DE.search(text)
        if m:
            valor = _parse_valor(m.group(1))
            nome = m.group(2).strip().rstrip(".")
            if valor is None:
                return None
            return make_transaction(
                banco_app=banco,
                movement_type=PIX_RECEBIDO,
                valor=valor,
                origem_destino=nome,
                notificacao_original=text,
            )

        # Outgoing Pix (débito).
        m = _PIX_ENVIADO.search(text)
        if m:
            valor = _parse_valor(m.group(1))
            nome = m.group(2).strip().rstrip(".")
            if valor is None:
                return None
            return make_transaction(
                banco_app=banco,
                movement_type=PIX_ENVIADO,
                valor=valor,
                origem_destino=nome,
                notificacao_original=text,
            )

        return None
