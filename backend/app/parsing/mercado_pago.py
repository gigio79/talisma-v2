from __future__ import annotations

import re

from app.parsing.base import DEBITO, BaseParser, ParsedTransaction, _parse_valor, make_transaction

# Mercado Pago payment:
#   "Mercado Pago • agoraVocê pagou PADARIA DO ZÉ. Debitamos R$ 40,00 da sua conta."
_PAGAMENTO = re.compile(
    r"(?:Mercado\s+Pago\s*[•·]\s*agora)?"
    r"\s*Vo[cç][eéê]\s+pagou\s+(.+?)\s+Debitamos\s+R\$\s*([\d.,]+)\s+da\s+sua\s+conta",
    re.IGNORECASE | re.DOTALL,
)


class MercadoPagoParser(BaseParser):
    app_name = "Mercado Pago"
    aliases = ("MercadoPago", "Mercado Pago Conta")

    def matches(self, text: str) -> bool:
        lower = text.lower()
        return "mercado pago" in lower or "mercadopago" in lower

    def parse(self, text: str, sender: str = "", app_hint: str = "") -> ParsedTransaction | None:
        banco = app_hint or self.app_name

        m = _PAGAMENTO.search(text)
        if m:
            valor = _parse_valor(m.group(2))
            if valor is None:
                return None
            estabelecimento = m.group(1).strip().rstrip(".")
            return make_transaction(
                banco_app=banco,
                movement_type=DEBITO,
                valor=valor,
                origem_destino=estabelecimento,
                notificacao_original=text,
            )

        return None
