from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ParsedTransaction:
    """Structured output from a notification parser.

    All fields use Portuguese labels for consistency with the existing
    codebase (transactions, accounts, etc.).
    """

    banco_app: str
    tipo: str  # "credit" | "debit"
    valor: Decimal
    origem_destino: str
    descricao: str
    notificacao_original: str


class BaseParser:
    """Base class for notification parsers.

    Subclasses must implement ``matches`` and ``parse``.
    The ``app_name`` class attribute identifies the bank/app this parser
    handles (e.g. ``"Neon"``, ``"PicPay"``).
    """

    app_name: str = ""

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
