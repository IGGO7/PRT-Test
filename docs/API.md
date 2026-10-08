# Contrato da API (o mesmo da interface do Claude Design)

Mesmo domínio da interface, JSON UTF-8, sessão por cookie `vitalis_sid` (HttpOnly). Toda rota devolve a **visão
completa da sessão** (`GET /api/state`), e a interface apenas re-renderiza. Implementação: `vitalis/service.py`.

| Método | Rota | Corpo | Efeito |
|---|---|---|---|
| GET | `/api/state` | — | Visão da sessão: `proposal`, `approval`, `operation`, `inbox`, `catalog`, `test_mode`, `version`, `erp_condicoes` |
| GET | `/api/examples` | — | Os 4 e-mails originais do dataset (lidos dos `.eml`) para pré-preencher o formulário |
| GET | `/api/health` | — | Saúde, modelo de IA configurado, data de negócio |
| POST | `/api/inbox` | `{mode:"dataset_example", example_id}` ou `{mode:"manual_simulation", sender, subject, body, csv?, csv_name?, derived_from_example?}` | Simula o recebimento (não analisa) |
| POST | `/api/inbox/close` | `{}` | Volta à caixa de entrada |
| POST | `/api/analyze` | `{item_id}` | **Agente de IA** lê a mensagem → `proposal` em `stage: INTERPRETACAO`; reabre se já analisada |
| POST | `/api/review` | `{proposal_id, expected_version, changes{}, confirmations[], absent[], acknowledged_warning_ids[], decision?, route?, reason_code?, note?}` | Correções, confirmações, ciência de avisos, encaminhamento (`decision:"ESCALAR"`) ou rejeição (`"REJEITAR"`) |
| POST | `/api/interpretation/close` | mesmo corpo do review | 422 `INTERPRETATION_OPEN` se restar dado "a verificar"; senão `stage: REGRAS` |
| POST | `/api/rules/close` | `{proposal_id, expected_version, apply_auto}` | `stage: CONCLUSAO`; encaminhamento/bloqueio automático só aqui |
| POST | `/api/approve` | `{proposal_id, revision_hash, expected_version, approver_id}` | 403 `ALCADA_INSUFICIENTE`; 409 se não estiver pronta |
| POST | `/api/erp/register` | `{proposal_id, approval_id, expected_version}` | Monta o payload do schema, `Idempotency-Key` UUID v4, até 3 tentativas em 503 com a mesma chave |
| POST | `/api/test-mode` | `{erp_fail_next?, erp_random_503?, auto_conclusion?}` | Controles do Modo técnico |
| POST | `/api/reset` | `{}` | Reinicia só a sessão atual a partir do snapshot de 30/09/2026 |

Erros: `{status, code, message, retryable, findings[], correlation_id}`. Concorrência: `expected_version` divergente → 409 `VERSION_CONFLICT`.

## Campo extraído (`proposal.fields[]`)

`key` (fornecedor, categoria, lojas, inicio, fim, contrapartida, desconto, verba) · `value` · `raw_value` (trecho da fonte) ·
`source_location` · `origin_kind` (EXPLICIT, NORMALIZED, INFERRED, CONFLICT, MISSING, HUMAN_CORRECTED) ·
`confirmation_status` (NOT_REQUIRED, PENDING, CONFIRMED, ABSENT) · `reason`.

Regras de verificação (D06): fornecedor, categoria e lojas sempre exigem confirmação (correspondência com o cadastro);
qualquer dado inferido, normalizado, em conflito ou ausente também. Só o que foi lido **literalmente** da fonte — com o trecho
localizado no texto — dispensa verificação. Evidência citada pelo modelo que não existe no texto é descartada.

## Apontamento (`proposal.findings[]`)

`id, rule_id, severity (BLOCKER|REQUIRES_CONFIRMATION|WARNING|INFO), message, affected_fields[], resolution_owner (REVISOR|EXTERNO|NORMATIVO), resolution_actions[], acknowledged`.
Regras em `vitalis/engine.py` (porte 1:1 do protótipo): RB01, RB02, RB03, RB04, RB05, RB06, RB07, RB08, RB10, RB13, RB14, RB16, D05, SEG.
