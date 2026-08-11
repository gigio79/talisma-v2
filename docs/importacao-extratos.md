# Guia: Importação de Extratos / Dados no Talismã

## 1. Conceito

A **Importação de Extratos** é a funcionalidade que permite carregar um arquivo de
extrato (emitido pelo banco, por um app de finanças ou por outro sistema) e
transformar automaticamente cada linha em uma **transação** dentro do workspace do
Talismã — passando por um fluxo de **pré-visualização, revisão e confirmação**
antes de gravar.

Não confunda com o parsing de notificações MacroDroid (webhook
`/api/webhooks/macrodroid`): são **canais distintos**. O webhook recebe textos de
notificação em tempo real; a importação recebe **arquivos** (OFX/CSV/QIF/CAMT) de
forma reativa e em lote.

## 2. Finalidade e Benefícios

- **Elimina a digitação manual**: centenas de lançamentos viram transações em
  segundos, com menos erro humano.
- **Entrada em massa precisa**: valores, datas e descrições são extraídos e
  normalizados do arquivo.
- **Revisão antes de gravar**: você escolhe a **conta destino**, ajusta
  **categorias**, inclui/exclui linhas e vê o impacto no saldo **antes** de
  confirmar.
- **Proteção contra duplicidade**: a importação detecta e **pula** transações que
  já existem na conta.
- **Vínculo automático**: compras que cumprem uma recorrência agendada
  (fatura/placeholder) são atualizadas no lugar de criar duplicata.
- **Rastreabilidade**: cada importação vira um **log** (data, arquivo, formato,
  conta, quantidade, créditos/débitos) com opção de **desfazer** (undo).
- **Múltiplas moedas**: quando o extrato está em moeda estrangeira, o valor é
  convertido automaticamente (ou com a taxa fornecida no CSV).

## 3. Funcionamento — o que acontece ao processar um arquivo

O fluxo é dividido em **duas etapas**: pré-visualização (preview) e importação
(commit).

### 3.1 Upload e detecção de formato

1. Você acessa a tela **Importar** (`/import`), arrasta/solta o arquivo ou clica
   para selecionar (`accept=".ofx,.qfx,.csv,.qif,.xml,.camt"`).
2. O frontend envia via `multipart/form-data` para `POST /api/transactions/import/preview`.
3. O backend lê os bytes e detecta o formato **pela extensão do arquivo**; se não
   reconhecer, tenta **por conteúdo** na ordem OFX → QIF → CAMT → CSV.

### 3.2 Parsing e mapeamento de campos

- **OFX/QFX**: biblioteca `ofxparse`; usa o `FITID` como identificador externo;
  ignora linhas de saldo; descrição = `memo` ou `payee`.
- **QIF**: parser manual; descrição = `payee` ou `memo`.
- **CAMT (ISO 20022)** `.xml/.camt`: parser manual para CAMT.052 e CAMT.053;
  entradas `PDNG` (pendentes) são descartadas para não virarem duplicata quando
  forem `BOOK`.
- **CSV**: parser manual com auto-detecção de **delimitador** (`;` `,` tab `|`
  via Sniffer) e de **colunas** (nomes em PT/EN). Permite **mapeamento manual**
  quando a auto-detecção falha.
- Em todos: data, valor, descrição, tipo débito/crédito, moeda e payee são
  extraídos e **normalizados** (ex.: `R$ 1.442,20` ↔ `1442.20`).

### 3.3 Enriquecimento e revisão

- As transações são enriquecidas com **sugestões de categoria** (regras ativas do
  workspace).
- O frontend monta uma **tabela de revisão** com filtros, busca, exclusão por
  checkbox e edição de categoria.
- Você escolhe a **conta destino**, opções de CSV (formato de data, inverter
  sinal, coluna entrada/saída, dedup) e o resumo mostra **total, incluídas,
  excluídas e impacto no saldo**.
- Qualquer mudança nas opções re-executa o preview.

### 3.4 Confirmação (commit)

1. `POST /api/transactions/import` (JSON) com `account_id`, a lista de transações
   incluídas, `detected_format` e `detect_duplicates`.
2. O backend:
   - Valida que a **conta pertence ao workspace** (senão 404) e que o usuário tem
     permissão de escrita;
   - Cria o **ImportLog** (com totais de crédito/débito);
   - Para cada transação: **dedup** → **payee** → **matching recorrente** →
     **categorização** → criação da `Transaction` → data efetiva (cartão de
     crédito) → regras → conversão FX;
   - Grava tudo e responde `{imported, skipped, excluded, import_log_id}`.
3. O frontend atualiza os dados financeiros, mostra o resultado e limpa a revisão.

### 3.5 Histórico e desfazer

- `GET /api/import-logs` lista as importações (com nome da conta).
- `DELETE /api/import-logs/{id}` **desfaz**: remove as transações daquele import e
  o log.

## 4. Critérios e Requisitos

### 4.1 Formatos suportados

| Formato | Extensões | Como é processado |
|---|---|---|
| **OFX / QFX** | `.ofx`, `.qfx` | `ofxparse`; usa `FITID`; ignora saldos |
| **QIF** | `.qif` | Parser próprio (UTF-8-sig, fallback Latin-1) |
| **CAMT 052/053** | `.xml`, `.camt` | Parser próprio; ignora pendentes |
| **CSV** | `.csv` | Parser próprio; auto-detecção + mapeamento manual |

### 4.2 Regras de validação

- **Conta**: deve existir e pertencer ao workspace ativo (`Account.workspace_id`
  ou a `BankConnection` da conta).
- **Permissão**: papel com escrita no workspace (owner/editor/manager).
- **CSV — colunas obrigatórias**:
  - `date` e `description` obrigatórias;
  - valor: `amount` **ou** o par `inflow`/`outflow` (entrada/saída);
  - colunas aceitas em PT/EN (ex.: `data`, `descricao`, `valor`, `tipo`,
    `categoria`, `moeda`, `taxa`…).
- **CSV — auto-detecção falhou**: o preview responde **200 com `parse_error`**
  (soft failure) e a UI oferece o **mapeamento manual** das colunas. Falha
  completa de parsing → HTTP 400.
- **Datas**: aceitas nos formatos `DD/MM/YYYY`, `MM/DD/YYYY`, `YYYY-MM-DD` ou
  detectadas automaticamente; linhas com data inválida são **puladas**.
- **Valores**: normalização de `R$` e separadores BR/internacional; valor sempre
  armazenado como positivo + tipo débito/crédito.
- **Codificação**: CSV/QIF lidos como UTF-8 (com BOM); QIF com fallback Latin-1.

### 4.3 Detecção de duplicatas (dedup)

- **OFX/QFX** (tem `FITID`): busca por `conta + external_id + data` — a data é
  exigida de propósito, porque bancos brasileiros **reutilizam o FITID** a cada
  parcela da fatura.
- **CSV/QIF/CAMT** (sem `FITID`): busca por `conta + data + valor + tipo +
  descrição`.
- A dedup é **sempre ativa** para OFX/QIF/CAMT; para **CSV** há um checkbox
  (`detect_duplicates`) — desligado, o CSV não deduplica.

### 4.4 Pré-requisitos e limites

- **Conta existente**: sem conta correspondente no workspace, a importação não é
  possível (a conta deve ser criada antes).
- **Sem limite de tamanho** de arquivo nem de número de transações por importação
  (o arquivo é lido em memória no preview).
- **Moeda**: usada a precedência CSV → conta → moeda padrão do usuário; moeda
  estrangeira sem taxa dispara **conversão automática** (FX).
- **Recorrências**: uma cobrança que cumpre um placeholder/agendamento da mesma
  conta, mesmo tipo, mesmo valor e descrição similar (≥0,6) é vinculada à
  recorrência e avança o ciclo.
