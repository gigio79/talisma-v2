from __future__ import annotations

from dataclasses import replace

from app.parsing.base import (
    _VALOR_EM_BRL,
    ESTABELECIMENTO_NAO_IDENTIFICADO,
    PENDENTE_REVISAO,
    BaseParser,
    ParsedTransaction,
    _parse_valor,
)
from app.parsing.generic import GenericParser
from app.parsing.google_wallet import GoogleWalletParser
from app.parsing.infinitepay import InfinitePayParser
from app.parsing.mercado_pago import MercadoPagoParser
from app.parsing.neon import NeonParser
from app.parsing.nubank import NubankParser
from app.parsing.picpay import PicPayParser

# Ordered list: more specific parsers first, generic last.
_PARSERS: list[BaseParser] = [
    NeonParser(),
    PicPayParser(),
    InfinitePayParser(),
    NubankParser(),
    MercadoPagoParser(),
    GoogleWalletParser(),
    GenericParser(),  # always last — fallback
]


def _normalize(result: ParsedTransaction | None) -> ParsedTransaction | None:
    """Post-process a parser result to enforce the spec fallbacks.

    - A ``None`` value means the parser could not extract a value — the
      caller must NOT create a transaction (log + alert admin instead).
    - An empty establishment with a value present becomes the "Estabelecimento
      não identificado" placeholder and flags the transaction for review.
    """
    if result is None:
        return None
    if result.valor is None:
        return None
    if not result.origem_destino.strip():
        return replace(
            result,
            origem_destino=ESTABELECIMENTO_NAO_IDENTIFICADO,
            precisa_revisao=True,
        )
    return result


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
        ``None`` when no parser could extract a value — in that case the
        caller should NOT create a transaction. When a value is found but no
        structured match exists, a minimal transaction flagged
        ``precisa_revisao=True`` is returned instead.
    """
    # If an app hint is given, try that parser first
    if app:
        for parser in _PARSERS:
            aliases = {a.lower() for a in parser.aliases}
            if parser.app_name.lower() == app.lower() or app.lower() in aliases:
                result = parser.parse(text, sender, app_hint=app)
                if result is not None:
                    return _normalize(result)

    # Try all parsers in order
    for parser in _PARSERS:
        if parser.matches(text):
            result = parser.parse(text, sender, app_hint=app)
            if result is not None:
                return _normalize(result)

    # Final fallback: a value exists but no structured match — create a
    # minimal transaction flagged for manual review (spec fallback).
    m = _VALOR_EM_BRL.search(text)
    if m:
        valor = _parse_valor(m.group(1))
        if valor is not None:
            banco = app or "Desconhecido"
            return ParsedTransaction(
                banco_app=banco,
                tipo="debit",
                valor=valor,
                origem_destino=ESTABELECIMENTO_NAO_IDENTIFICADO,
                descricao=f"{PENDENTE_REVISAO} - {banco}",
                notificacao_original=text,
                movement_type="",
                precisa_revisao=True,
            )

    return None


def list_parsers() -> list[str]:
    """Return the list of registered parser app names."""
    return [p.app_name for p in _PARSERS]
