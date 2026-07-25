# Parsing de Notificações do MacroDroid

Módulo isolado para reconhecimento e parsing de notificações bancárias recebidas via MacroDroid.

## Estrutura

```
backend/app/parsing/
├── __init__.py      # Exporta parse_notification() e list_parsers()
├── base.py          # BaseParser + ParsedTransaction (dataclass)
├── neon.py          # Parser Neon
├── picpay.py        # Parser PicPay
├── generic.py       # Parser genérico (fallback)
└── registry.py      # Registro e orquestração dos parsers
```

## Uso

```python
from app.parsing import parse_notification

text = "Auto-created from MacroDroid: Neon • agoraPix recebidoVocê recebeu um Pix de João Silva no valor de R$ 50,00"
result = parse_notification(text, app="Neon")

if result:
    print(result.banco_app)      # "Neon"
    print(result.tipo)           # "credit"
    print(result.valor)          # Decimal("50.00")
    print(result.origem_destino) # "João Silva"
    print(result.descricao)      # "Pix recebido - João Silva"
```

## Adicionar um novo parser

1. Crie um arquivo em `backend/app/parsing/seubanco.py`:

```python
from __future__ import annotations
import re
from decimal import Decimal
from typing import Optional
from app.parsing.base import BaseParser, ParsedTransaction

class SeuBancoParser(BaseParser):
    app_name = "SeuBanco"

    def matches(self, text: str) -> bool:
        return "seubanco" in text.lower()

    def parse(self, text: str, sender: str = "") -> Optional[ParsedTransaction]:
        # Implemente o parsing aqui
        m = re.search(r"seu padrao", text)
        if m:
            return ParsedTransaction(
                banco_app=self.app_name,
                tipo="credit",  # ou "debit"
                valor=Decimal("100.00"),
                origem_destino="Nome",
                descricao="Descrição",
                notificacao_original=text,
            )
        return None
```

2. Registre em `backend/app/parsing/registry.py`:

```python
from app.parsing.seubanco import SeuBancoParser

_PARSERS = [
    NeonParser(),
    PicPayParser(),
    SeuBancoParser(),  # Adicione antes do GenericParser
    GenericParser(),   # sempre último
]
```

## Padrões suportados

| App | Padrão | Tipo |
|-----|--------|------|
| Neon | `Neon • agoraPix enviado...` | débito |
| Neon | `Neon • agoraPix recebido...` | crédito |
| PicPay | `PicPay • agora...enviou um Pix...` | débito |
| Genérico | `Pix recebido de X no valor de R$ Y` | crédito |
| Genérico | `Transferência recebida de X - R$ Y` | crédito |
| Genérico | `Compra aprovada no cartão final X - R$ Y - Z` | débito |
| Genérico | `Notificação bancária: R$ X - Y` | débito |

## Notas

- O parser genérico (`GenericParser`) é sempre o último a ser tentado
- Quando o parâmetro `app` é fornecido a `parse_notification()`, o parser correspondente é tentado primeiro
- Campos do `ParsedTransaction`:
  - `banco_app`: Nome do banco/app de origem
  - `tipo`: `"credit"` ou `"debit"`
  - `valor`: Valor da transação (Decimal)
  - `origem_destino`: Nome da pessoa/estabelecimento
  - `descricao`: Descrição formatada da transação
  - `notificacao_original`: Texto bruto da notificação
