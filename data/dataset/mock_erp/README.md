# Simulação do ERP — contrato da API de condições comerciais

Esta pasta descreve o contrato da API do ERP para condições comerciais.

**Não há servidor rodando.** Este diretório é uma simulação documental: o contrato,
o schema e respostas reais capturadas do sistema. A sua solução deve reproduzir
este comportamento da forma que você julgar adequada.

```
mock_erp/
├── README.md                        este arquivo
├── schema/
│   └── condicao_post.schema.json    formato aceito no cadastro
├── respostas/                       respostas capturadas em 30/09/2026 08:15
│   ├── GET_fornecedores.json
│   ├── GET_lojas.json
│   └── GET_condicoes.json
└── exemplos/                        exemplos ilustrativos de requisição e resposta
```

Base URL: `https://erp.vitalis.internal/api/v1`

Autenticação: fora do escopo desta simulação.

---

## Consultas

### `GET /fornecedores`

Cadastro de fornecedores, com situação atual.
Resposta capturada: `respostas/GET_fornecedores.json`.

### `GET /lojas`

Cadastro de lojas da rede.
Resposta capturada: `respostas/GET_lojas.json`.

### `GET /condicoes?status=VIGENTE`

Condições comerciais vigentes, **em tempo real**, com o estado atual de cada
registro no momento da consulta.
Resposta capturada: `respostas/GET_condicoes.json`.

---

## Cadastro

### `POST /condicoes`

Cadastra uma condição comercial.

**Headers obrigatórios**

| Header | Valor |
|---|---|
| `Content-Type` | `application/json` |
| `Idempotency-Key` | UUID v4 |

Requisições repetidas com a mesma `Idempotency-Key` em até 24 horas retornam a
resposta original, sem novo cadastro.

**Corpo**

Definido em `schema/condicao_post.schema.json`. Resumo:

| Campo | Obrigatório | Observação |
|---|---|---|
| `cod_fornecedor` | sim | `FORN-###` |
| `cod_categoria` | sim | enum do schema |
| `tipo` | sim | `DESCONTO_PERCENTUAL` ou `VERBA_EXPOSICAO` |
| `lojas` | sim | lista de `LOJA-###`, ou `["REDE"]` |
| `percentual` | se desconto | número, até 2 casas decimais |
| `valor` | se verba | reais, até 2 casas decimais |
| `data_inicio` | sim | `AAAA-MM-DD` |
| `data_fim` | sim | `AAAA-MM-DD` |
| `contrapartida` | não | texto livre, até 500 caracteres |
| `cod_condicao_substituida` | não | encerra a condição referenciada na véspera de `data_inicio` |
| `aprovador` | sim | `{ matricula, nome }` |
| `origem` | sim | `{ tipo: "EMAIL", referencia }` |

Cada requisição cadastra **uma** condição.

---

## Respostas

| HTTP | `error` | Significado |
|---|---|---|
| 201 | — | Condição cadastrada. Retorna `cod_condicao`. |
| 400 | `MISSING_IDEMPOTENCY_KEY` | Header `Idempotency-Key` ausente. |
| 422 | `VALIDATION_ERROR` | Corpo fora do schema. Retorna a lista de campos. |
| 422 | `SUPPLIER_NOT_ACTIVE` | Fornecedor não possui cadastro ativo. |
| 429 | `RATE_LIMIT_EXCEEDED` | Limite de requisições excedido. Header `Retry-After` em segundos. |
| 503 | `SERVICE_UNAVAILABLE` | Sistema em manutenção. |

Formato de erro:

```json
{ "error": "SUPPLIER_NOT_ACTIVE", "message": "Fornecedor não possui cadastro ativo" }
```

Exemplos completos em `exemplos/`.

---

## Comportamento conhecido do ambiente

- Limite de **10 requisições por minuto** por cliente, somando todos os endpoints.
- O ambiente passa por janelas de manutenção frequentes: em média, **1 a cada 5
  chamadas** retorna `503`.
