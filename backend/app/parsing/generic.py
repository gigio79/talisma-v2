from __future__ import annotations

import re

from app.parsing.base import (
    DEBITO,
    PIX_ENVIADO,
    PIX_RECEBIDO,
    BaseParser,
    ParsedTransaction,
    _parse_valor,
    make_transaction,
)

# Generic Pix patterns (no app prefix):
#   "Pix recebido de João Silva no valor de R$ 50,00"
#   "Pix recebido de Maria Santos no valor de R$ 100,00"
#   "Pix recebidoVoce recebeu um Pix de Giovanni Bispo..."
#   "Você recebeu um Pix de Giovanni Bispo..."

_PIX_GENERIC = re.compile(
    r"\s*(?:Vo[cç][eéê]\s+)?recebeu(?:u)?\s+um\s+Pix\s+de\s+"
    r"(.+?)"
    r"(?:\s+CPF\s+[\d.*\-\s]+)?"
    r"(?:\s*no\s+valor\s+de|\s+de)\s+R\$\s*([\d.,]+)",
    re.IGNORECASE | re.DOTALL,
)

_PIX_GENERIC_SIMPLE = re.compile(
    r"Pix\s+recebido\s+de\s+(.+?)\s+(?:no\s+valor\s+de|de)\s+R\$\s*([\d.,]+)",
    re.IGNORECASE,
)

_PIX_ENVIADO_GENERIC = re.compile(
    r"\s*(?:Vo[cç][eéê]\s+)?fez\s+um\s+Pix\s+no\s+valor\s+de\s+R\$\s*([\d.,]+)\s+para\s+"
    r"(.+?)(?:\.|$)",
    re.IGNORECASE | re.DOTALL,
)

_PIX_RECEBIDO_DE_GENERIC = re.compile(
    r"(.+?)\s+enviou\s+um\s+Pix\s+para\s+voc[eêê]"
    r".*?\s*(?:Vo[cç][eéê]\s+)?recebeu(?:u)?\s+um\s+Pix\s+de\s+R\$\s*([\d.,]+)",
    re.IGNORECASE | re.DOTALL,
)

_TRANSFERENCIA = re.compile(
    r"[Tt]ransfer[eê]ncia\s+recebida\s+de\s+(.+?)\s*[-–]\s*R\$\s*([\d.,]+)",
    re.IGNORECASE,
)

_COMPRA_CARTAO = re.compile(
    r"Compra\s+aprovada\s+no\s+cart[aã]o\s+final\s+(\d+)\s*[-–]\s*R\$\s*([\d.,]+)\s*[-–]\s*(.+?)(?:\.|$)",
    re.IGNORECASE,
)

_NOTIFICACAO_BANCARIA = re.compile(
    r"Notifica[cç][aã]o\s+banc[aá]ria:\s*R\$\s*([\d.,]+)\s*[-–]\s*(.+?)(?:\.|$)",
    re.IGNORECASE,
)


class GenericParser(BaseParser):
    """Fallback parser for notifications without a known app prefix."""

    app_name = "Desconhecido"

    def matches(self, text: str) -> bool:
        # Generic always matches — it's the fallback
        return True

    def parse(self, text: str, sender: str = "", app_hint: str = "") -> ParsedTransaction | None:
        banco = app_hint or self.app_name

        # Pix recebido (full pattern)
        m = _PIX_GENERIC.search(text)
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

        # Simple "Pix recebido de X no valor de R$ Y"
        m = _PIX_GENERIC_SIMPLE.search(text)
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

        # Pix enviado (débito): "Você fez um Pix no valor de R$ X para NOME"
        m = _PIX_ENVIADO_GENERIC.search(text)
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

        # Pix recebido multi-linha: "NOME enviou um Pix para você... Você recebeu um Pix de R$X"
        m = _PIX_RECEBIDO_DE_GENERIC.search(text)
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

        # Transferência recebida
        m = _TRANSFERENCIA.search(text)
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

        # Compra aprovada no cartão
        m = _COMPRA_CARTAO.search(text)
        if m:
            valor = _parse_valor(m.group(2))
            if valor is None:
                return None
            estabelecimento = m.group(3).strip()
            cartao_final = m.group(1)
            return make_transaction(
                banco_app=banco,
                movement_type=DEBITO,
                valor=valor,
                origem_destino=estabelecimento,
                notificacao_original=text,
                cartao_final=cartao_final,
            )

        # Notificação bancária genérica
        m = _NOTIFICACAO_BANCARIA.search(text)
        if m:
            valor = _parse_valor(m.group(1))
            if valor is None:
                return None
            descricao = m.group(2).strip()
            return make_transaction(
                banco_app=banco,
                movement_type=DEBITO,
                valor=valor,
                origem_destino=descricao,
                notificacao_original=text,
            )

        return None
