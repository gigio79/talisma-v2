from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Granular movement types from the MacroDroid spec.
PIX_RECEBIDO = "pix_recebido"
PIX_ENVIADO = "pix_enviado"
DEBITO = "debito"
CREDITO = "credito"

# Mapping of granular movement type to the model's `type` (debit/credit),
# which drives balance/P&L math.
MOVEMENT_TO_TIPO = {
    PIX_RECEBIDO: "credit",
    PIX_ENVIADO: "debit",
    DEBITO: "debit",
    CREDITO: "credit",
}

# Fallback description used when no structured parser matched but a value
# was still found in the text (spec: "Transação pendente de revisão - {app}").
PENDENTE_REVISAO = "Transação pendente de revisão"

# Placeholder establishment when a value was parsed but no establishment.
ESTABELECIMENTO_NAO_IDENTIFICADO = "Estabelecimento não identificado"


def _parse_valor(raw: str) -> Decimal | None:
    """Convert a Brazilian-formatted currency string to Decimal.

    Accepts "R$ 30,00", "12.345,67", "1.234,56", "0,65", "R$ 12,80".
    Returns ``None`` when the raw string is not a valid positive amount.
    """
    raw = raw.strip()
    if not raw:
        return None
    # Brazilian format: dots are thousands separators, comma is the decimal
    # separator. Normalize to a dot-decimal string.
    if "," in raw:
        cleaned = raw.replace(".", "").replace(",", ".")
    else:
        cleaned = raw
    try:
        valor = Decimal(cleaned)
    except InvalidOperation:
        return None
    if valor <= 0:
        return None
    return valor


_VALOR_EM_BRL = re.compile(r"R\$\s*([\d][\d.,]*)", re.IGNORECASE)


def build_description(
    movement_type: str,
    origem_destino: str,
    app: str,
) -> str:
    """Build the standardized description from the MacroDroid spec.

    The amount is NOT included — it is displayed separately from the
    transaction's `amount` field.
    - Pix recebido de {nome} via {app}
    - Pix enviado para {nome} via {app}
    - Compra em {estabelecimento} via {app} (débito/crédito)
    """
    if movement_type == PIX_RECEBIDO:
        return f"Pix recebido de {origem_destino} via {app}"
    if movement_type == PIX_ENVIADO:
        return f"Pix enviado para {origem_destino} via {app}"
    if movement_type in (DEBITO, CREDITO):
        return f"Compra em {origem_destino} via {app}"
    # Unknown movement type — fall back to a generic label.
    return f"Movimentação em {origem_destino} via {app}"


@dataclass(frozen=True)
class ParsedTransaction:
    """Structured output from a notification parser.

    All fields use Portuguese labels for consistency with the existing
    codebase (transactions, accounts, etc.).

    ``tipo`` is the model-facing value ("credit"/"debit") derived from the
    granular ``movement_type``. ``valor`` may be ``None`` when the parser
    could not extract a value — in that case the caller must NOT create a
    transaction (spec: log + alert admin).
    """

    banco_app: str
    tipo: str  # "credit" | "debit"
    valor: Decimal | None
    origem_destino: str
    descricao: str
    notificacao_original: str
    movement_type: str = ""
    cartao_final: str | None = None
    precisa_revisao: bool = False


def make_transaction(
    *,
    banco_app: str,
    movement_type: str,
    valor: Decimal,
    origem_destino: str,
    notificacao_original: str,
    cartao_final: str | None = None,
    precisa_revisao: bool = False,
) -> ParsedTransaction:
    """Convenience constructor that derives ``tipo`` and the description."""
    return ParsedTransaction(
        banco_app=banco_app,
        tipo=MOVEMENT_TO_TIPO[movement_type],
        valor=valor,
        origem_destino=origem_destino,
        descricao=build_description(
            movement_type, origem_destino, banco_app
        ),
        notificacao_original=notificacao_original,
        movement_type=movement_type,
        cartao_final=cartao_final,
        precisa_revisao=precisa_revisao,
    )


class BaseParser:
    """Base class for notification parsers.

    Subclasses must implement ``matches`` and ``parse``.
    The ``app_name`` class attribute identifies the bank/app this parser
    handles (e.g. ``"Neon"``, ``"PicPay"``).
    """

    app_name: str = ""
    # Alternate names that the webhook payload `app` field may use to refer
    # to this app (e.g. "Carteira do Google" for Google Wallet).
    aliases: tuple[str, ...] = ()

    def matches(self, text: str) -> bool:
        """Return True if this parser can handle *text*."""
        raise NotImplementedError

    def parse(self, text: str, sender: str = "", app_hint: str = "") -> ParsedTransaction | None:
        """Parse *text* and return a ``ParsedTransaction`` or ``None``.

        Parameters
        ----------
        text:
            Raw notification text.
        sender:
            Sender identifier from the notification.
        app_hint:
            App name hint from the webhook payload (e.g. "PicPay", "Neon").
            When provided, used in description instead of ``self.app_name``.
        """
        raise NotImplementedError
