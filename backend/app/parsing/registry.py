from __future__ import annotations

from app.parsing.base import BaseParser, ParsedTransaction
from app.parsing.generic import GenericParser
from app.parsing.neon import NeonParser
from app.parsing.nubank import NubankParser
from app.parsing.picpay import PicPayParser

# Ordered list: more specific parsers first, generic last.
_PARSERS: list[BaseParser] = [
    NeonParser(),
    PicPayParser(),
    NubankParser(),
    GenericParser(),  # always last — fallback
]


def parse_notification(
    text: str,
    app: str = "",
    sender: str = "",
) -> ParsedTransaction | None:
    """Parse a raw notification and return structured transaction data.

    Parameters
    ----------
    text:
        Raw notification text (e.g. from MacroDroid).
    app:
        Bank/app name hint (e.g. ``"Neon"``, ``"PicPay"``).  When provided
        and a matching parser exists it is tried first. Also used in the
        description field when available.
    sender:
        Sender identifier from the notification, if available.

    Returns
    -------
    ParsedTransaction or None
        ``None`` if no parser could extract the data.
    """
    # If an app hint is given, try that parser first
    if app:
        for parser in _PARSERS:
            if parser.app_name.lower() == app.lower():
                result = parser.parse(text, sender, app_hint=app)
                if result is not None:
                    return result

    # Try all parsers in order
    for parser in _PARSERS:
        if parser.matches(text):
            result = parser.parse(text, sender, app_hint=app)
            if result is not None:
                return result

    return None


def list_parsers() -> list[str]:
    """Return the list of registered parser app names."""
    return [p.app_name for p in _PARSERS]
