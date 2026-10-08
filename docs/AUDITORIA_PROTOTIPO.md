# Auditoria — protótipo (Claude Design) × briefing × Fonte Única de Verdade × dataset

Data: 08/10/2026. Protótipo: `Central_de_Condicoes_Comerciais_3.html` (handoff v5).
Fontes: briefing Pareto (`docs/BRIEFING.md`), SSoT v0.3.2, dataset original (`data/dataset`).

## Conforme (mantido como está)

| Tema | Evidência no protótipo | Fonte |
|---|---|---|
| Entrada por e-mail simulado em texto livre + CSV opcional; 4 exemplos carregáveis; nenhuma fila pré-carregada | Caixa de entrada começa vazia; exemplos só preenchem o formulário | Briefing "Entrada"; SSoT D01/D02 |
| Proveniência por campo (explícito, normalizado, inferido, conflito, ausente, corrigido) | `fields[].origin_kind` + rótulos | Briefing req. 2; SSoT §22 |
| Revisão humana campo a campo antes das regras; nada material preenchido em silêncio | Etapa 2 exige 0 dados "a verificar" | SSoT D06, RB16 |
| Regras determinísticas fora da IA (tetos, alçadas, vigência, sobreposição, fornecedor ativo) | `validate()` | Briefing "onde a IA não deve entrar"; SSoT §6.5 |
| Ciência explícita de cada aviso, invalidada por nova revisão | `acks` por `revision_hash` | SSoT D08, RB15 |
| Aprovação e cadastro como eventos separados; alçada por perfil simulado; 403 sem alçada | Diálogo em 2 passos | SSoT D03, D10 |
| Edição material invalida aprovação | `review()` | SSoT D12/RB11 |
| Substituição conservadora (mesmas lojas, sem encerramento parcial nem retroativo) | `RB10` | SSoT D07 |
| Gama: conflito 12%×12,5%, falta data final, desconto+verba bloqueados por lacuna normativa | `RB16`, `RB04`, `RB06:COMPONENTES` | SSoT §3.1, D11 |
| Alfa: 28% = exceção sazonal sem evidência → Diretoria | `RB13:SAZONAL` | SSoT §3.1, R1 |
| Nutrivida: inativo → Cadastro; instrução embutida tratada como dado | `RB01`, `SEG` | SSoT §3.1 |
| Aprovação/rejeição nunca automáticas; só bloqueio/encaminhamento por regra | `AUTO_RULES` | SSoT §4.4, §25 |
| Idempotency-Key UUID, replay 24 h, 409 de versão, sucesso só com 201 | `register()`, `erpPost()` | ERP README; SSoT D14–D16 |
| Escopo declarado na própria solução | "Sobre esta demonstração" | Briefing, Entrega §2 |
| Controles de falha só no Modo técnico; fluxo normal estável | `test_mode` | SSoT D21 |

## Desvios encontrados e correção aplicada

| # | Desvio | Por que importa | Correção |
|---|---|---|---|
| 1 | Extração por expressões regulares no navegador | Briefing: "A solução precisa usar agentes de IA" | Backend usa o agente (`create_agent` + OpenAI) e devolve o mesmo formato `fields[]` |
| 2 | Exemplos embutidos **alterados**: "Vitalis" trocado por "rede"/`rede.exemplo.com.br` no e-mail da Nutrivida, nomes de loja sem "VITALIS", quebra de linha final removida | SSoT §36.2: carregar exemplo deve manter os dados brutos reais do dataset | `public/dataset/fixtures.json` gerado a partir dos `.eml` originais; catálogo vem do ERP simulado do backend |
| 3 | Backend simulado em `localStorage` | Link público precisa de estado no servidor, isolado por avaliador (RNF05/06) | FastAPI + Supabase com cookie de sessão e controle de versão |
| 4 | Texto "Fora do escopo: leitura por modelo de IA nesta versão" | Deixa de ser verdade com o backend real | Texto atualizado |
| 5 | Limites divergentes (UI 8.000 caracteres/200 KB; backend 12.000/64 KB) | Mensagens de erro incoerentes | Backend alinhado a 8.000 caracteres e 200 KB |
| 6 | Sem indicação de espera durante a leitura pela IA (o simulado respondia em 0,9 s) | IA real leva segundos; sem feedback a pessoa repete o clique | Aviso "A IA está lendo a mensagem…" enquanto a análise roda |
| 7 | Dataset embutido no HTML (`window.CC_DATASET`) | Duas fontes de verdade para o mesmo dado | Removido; dados vêm do backend e dos arquivos originais |

## Pontos mantidos com justificativa (registrar na seção 08 do PDF)

- **Reenvio automático em 503 (até 3 tentativas, mesma chave).** A proposta lean de D16 previa só reenvio explícito; o
  briefing avalia "que a solução continue de pé quando o ambiente não coopera" e o README do ERP descreve 503 frequente.
  Três tentativas com a mesma Idempotency-Key não criam duplicata e não exigem fila nem worker. O reenvio explícito
  continua disponível. **D16 segue pendente de ratificação.**
- **Origem não verificada (RB07) como aviso, não bloqueio.** Na demonstração toda entrada é simulada; em produção deve bloquear (pendência 4 do handoff).
- **Limite de 10 req/min** aplicado às chamadas de cadastro ao ERP simulado; as consultas usam o snapshot da sessão (consultado em 30/09/2026 08:15), sem chamada adicional.
- **Comitê Comercial** fora dos perfis simulados: verba acima de R$ 20.000 fica bloqueada com encaminhamento externo.
- **Caixa de entrada da sessão** (P1 opcional na SSoT): mantida porque começa vazia e não pré-carrega cenários.
