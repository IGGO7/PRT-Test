# Dataset — Cadastro de condições comerciais

Data de referência do dataset: **30/09/2026**.

Todos os nomes, empresas, CNPJs, pessoas e valores são fictícios.

## Conteúdo

```
dataset/
├── emails/                   propostas recebidas pela caixa do time comercial
│   └── anexos/               arquivos citados nos e-mails
├── extrato_erp.csv           export de condições comerciais do ERP
├── Politica_Comercial.pdf    política comercial vigente
├── mock_erp/                 simulação do ERP (ver README próprio)
└── README.md                 este arquivo
```

## emails/

Mensagens no formato `.eml` (texto puro, UTF-8), como foram exportadas da caixa de
entrada. O nome do arquivo segue `AAAA-MM-DD_HHMM_remetente.eml`.

Quando um e-mail cita um anexo, o arquivo está em `emails/anexos/` com o mesmo nome
citado na mensagem.

## extrato_erp.csv

Export do módulo de condições comerciais do ERP, gerado automaticamente em
**29/09/2026 às 06:00**, no layout padrão da rotina de exportação.

| Característica | Valor |
|---|---|
| Separador | `;` |
| Decimal | vírgula |
| Datas | `AAAAMMDD` |
| Granularidade | uma linha por condição × loja |

| Coluna | Descrição |
|---|---|
| `COND_ID` | Código da condição no ERP |
| `COD_FORN` | Código do fornecedor |
| `NOME_FORN` | Razão social do fornecedor |
| `CATEG` | Código da categoria de produto |
| `COD_LOJA` | Código da loja. `REDE` indica todas as lojas |
| `TP_COND` | `DESC` = desconto percentual · `VERBA` = verba de exposição |
| `VLR_PCT` | Percentual de desconto (quando `DESC`) |
| `VLR_BRL` | Valor em reais (quando `VERBA`) |
| `DT_INI` | Início da vigência |
| `DT_FIM` | Fim da vigência |
| `STATUS` | `VIGENTE`, `ENCERRADA` ou `CANCELADA` |
| `DT_CAD` | Data do cadastro |
| `USR_CAD` | Usuário que cadastrou |

## Politica_Comercial.pdf

Documento oficial da Diretoria Comercial, na versão que circula internamente.

## mock_erp/

Simulação do ERP. O contrato está descrito em `mock_erp/README.md`.
