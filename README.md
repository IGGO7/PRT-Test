# Vitalis

Central de condições comerciais. Case técnico demonstrativo (Pareto AI Builder) com **dados fictícios**. Transforma o e-mail de um fornecedor
em uma decisão estruturada, verificável e pronta para cadastro, sem retirar a responsabilidade de quem autoriza.

> Fontes: briefing Pareto (`docs/BRIEFING.md`), *Fonte Única de Verdade v0.3.2* e o dataset original (`data/dataset`).
> A interface é o protótipo do Claude Design (`frontend/prototype.html`), ligado à API real por `scripts/build_frontend.py`.
> Alinhamento verificado em `docs/AUDITORIA_PROTOTIPO.md`; origem de cada dado (IA × regra) e provas em `docs/VERIFICACAO.md`.

## O que é real e o que é simulado

| Item | Situação |
|---|---|
| Interpretação do e-mail | **Real** — agente LangChain `create_agent` (runtime LangGraph) + OpenAI, saída validada por Pydantic |
| Regras de política | **Real** — código determinístico em `vitalis/engine.py` (porte 1:1 do protótipo), POL-COM-004 v3.2 em `vitalis/policy.py` |
| Persistência | **Real** — Supabase/PostgreSQL, uma linha JSONB por sessão com controle otimista de versão |
| ERP | Simulado — `vitalis/erp_mock.py` reproduz o contrato do dataset (idempotência 24 h, 400/422/429/503) |
| Aprovadores e alçadas | Simulados e declarados (sem autenticação corporativa) |
| Ingestão de e-mail | Simulada — formulário livre + 4 exemplos carregáveis; nada é enviado ou buscado |
| Data de negócio | Fixa em 30/09/2026 (o horário técnico real é preservado nos registros) |

## Estrutura

```
app.py                     entrypoint FastAPI (detectado pela Vercel)
vitalis/
  extraction.py            agente de IA (create_agent + ferramentas só de leitura) e contrato de saída
  interpretation.py        saída do agente → fields[] com evidência literal e o que exige verificação
  engine.py                regras RB01–RB16/D05/SEG, estados e conclusão (sem IA)
  service.py               rotas do contrato: caixa de entrada, interpretação, regras, aprovação, cadastro
  erp_mock.py              ERP simulado (POST /condicoes, idempotência 24 h, 422/429/503)
  store.py                 Supabase (PostgREST + controle de versão) e memória para testes
  dataset.py · policy.py · config.py · app.py
frontend/prototype.html    export do Claude Design (fonte da interface)
public/                    interface gerada (python scripts/build_frontend.py)
data/dataset/              fontes originais do dataset + transcrição da política
supabase/migrations/       schema da tabela demo_sessions
docs/                      briefing, auditoria do protótipo, contrato da API
scripts/                   build do frontend, smoke tests de Supabase e do modelo
tests/                     fluxos dos 4 cenários pela API + contrato do ERP (sem rede)
```

## Rodar localmente

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # preencha OPENAI_API_KEY e SUPABASE_SECRET_KEY
pytest                            # 89 testes, sem rede
BASE_URL=https://... python tests/e2e/prod_check.py   # 13 cenários com o agente real no site publicado
python scripts/build_frontend.py  # regenera public/ a partir de frontend/prototype.html
SERVE_PUBLIC=1 uvicorn app:app --reload   # http://localhost:8000 (exige OPENAI_API_KEY)
SERVE_PUBLIC=1 uvicorn tests.ui_harness:app --port 8020   # interface sem OpenAI (extração de teste)
python scripts/smoke_supabase.py  # prova navegador→API→persistência (Marco 0)
python scripts/smoke_llm.py       # spike do modelo nos 4 exemplos + Beta editado
```

Sem `SUPABASE_*`, o backend usa memória (apenas desenvolvimento — não atende RNF05 em serverless).

## Supabase

1. Aplique `supabase/migrations/20261008120000_demo_sessions.sql` (SQL Editor ou `supabase db push`).
2. Copie a **secret key** (`sb_secret_…`) em *Project Settings → API Keys* para `SUPABASE_SECRET_KEY`.
   A tabela tem RLS ligado e nenhuma política: chaves públicas não acessam nada.
3. Os dados do dataset **não** são copiados para tabelas: ficam no repositório (ADR-07) e cada sessão nasce
   com uma cópia do snapshot de 30/09/2026 (§7.5). O banco guarda só o estado mutável.

## Deploy (Vercel)

Importe o repositório; a Vercel detecta `app.py` (FastAPI) e serve `public/` pela CDN.
Configure as variáveis do `.env.example`. `vercel.json` define `maxDuration: 60` e inclui `data/**` no bundle.

## Decisões e pendências relevantes

- **Resiliência do cadastro:** até 3 tentativas automáticas em 503 com a mesma chave + reenvio explícito; após 24 h sem
  confirmação, reenvio bloqueado. Sem filas nem reconciliador (D16 lean, pendente de ratificação).
- **Gama/Alfa:** a conclusão correta é bloqueio/escalonamento documentado — a coexistência desconto+verba
  e a precedência da exceção sazonal são lacunas da política, não resolvidas pelo código.
- **Rate limit do ERP:** 10 req/min aplicado ao `POST /condicoes`; consultas usam o snapshot da sessão.
- **D23:** `gpt-5-nano` é candidato configurável por `OPENAI_MODEL`; a extração real ainda precisa do spike.
