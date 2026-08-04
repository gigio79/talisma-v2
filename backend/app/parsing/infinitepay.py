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

# InfinitePay PIX enviado:
#   "InfinitePay — Pix enviado\nPix enviado com sucesso! ✅\nVocê fez um Pix no valor de R$ 273,82 para CENCOSUD BRASIL ATACADO LTDA."
_PIX_ENVIADO = re.compile(
    r"(?:Vo[cç][eéê]\s+)?fez\s+um\s+Pix\s+no\s+valor\s+de\s+R\$\s*([\d.,]+)\s+para\s+"
    r"(.+?)(?:\.|$)",
    re.IGNORECASE | re.DOTALL,
)

# InfinitePay PIX recebido:
#   "NOME enviou um Pix para você\nVocê recebeu um Pix de R$X"
_PIX_RECEBIDO = re.compile(
    r"(.+?)\s+enviou\s+um\s+Pix\s+para\s+voc[eêê]"
    r".*?(?:Vo[cç][eéê]\s+)?recebeu(?:u)?\s+um\s+Pix\s+de\s+R\$\s*([\d.,]+)",
    re.IGNORECASE | re.DOTALL,
)


class InfinitePayParser(BaseParser):
    app_name = "InfinitePay"
    aliases = ("Infinite Pay",)

    def matches(self, text: str) -> bool:
        return "infinitepay" in text.lower()

    def parse(self, text: str, sender: str = "", app_hint: str = "") -> ParsedTransaction | None:
        banco = app_hint or self.app_name

        # PIX enviado
        m = _PIX_ENVIADO.search(text)
        if m:
            valor = _parse_valor(m.group(1))
            if valor is None:
                return None
            nome = m.group(2).strip().rstrip(".")
            return make_transaction(
                banco_app=banco,
                movement_type=PIX_ENVIADO,
                valor=valor,
                origem_destino=nome,
                notificacao_original=text,
            )

        # PIX recebido
        m = _PIX_RECEBIDO.search(text)
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

        return None
