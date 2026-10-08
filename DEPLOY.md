# Publicação — passo a passo

## 1. Supabase (obrigatório antes do primeiro acesso)

A tabela `demo_sessions` ainda não existe no projeto PRT-CASE. Sem ela, todas as telas mostram
"Persistência indisponível".

1. Abra o projeto **PRT-CASE** no Supabase → **SQL Editor** → **New query**.
2. Cole o conteúdo de `supabase/migrations/20261008120000_demo_sessions.sql` e clique **Run**.
3. Deve aparecer "Success. No rows returned".

## 2. GitHub (upload pelo navegador)

1. Descompacte o zip no computador.
2. No repositório **IGGO7/PRT-Test**: **Add file → Upload files**.
3. Arraste **o conteúdo** da pasta `PRT-Test` (não a pasta, nem o zip) para a página.
   - Inclua os arquivos ocultos: `.gitignore`, `.vercelignore`, `.python-version`, `.env.example`.
     No Mac, `Cmd + Shift + .` mostra arquivos ocultos no Finder; no Windows, Exibir → Itens ocultos.
   - Confira que as pastas `vitalis`, `public`, `data` e o arquivo `app.py` estão na raiz do repositório.
4. **Commit changes** na branch `main`. Se a Vercel estiver ligada ao repositório, o deploy começa sozinho.

## 3. Variáveis na Vercel (Production e Preview)

| Variável | Valor |
|---|---|
| `OPENAI_API_KEY` | sua chave da OpenAI |
| `OPENAI_MODEL` | `gpt-6-luna` (ou o modelo que você validar) |
| `SUPABASE_URL` | `https://gjmiwygmnqkampsxzquf.supabase.co` |
| `SUPABASE_SECRET_KEY` | a **secret key** (`sb_secret_...`), nunca a publishable |
| `STORAGE_BACKEND` | `supabase` |
| `APP_ENV` | `production` |
| `SESSION_COOKIE_SECURE` | `true` |

As demais do `.env.example` podem ficar sem valor. Depois de mudar variáveis, use **Redeploy**.

## 4. Conferir

1. Abra `https://SEU-PROJETO.vercel.app/api/health?deep=1`. O esperado é `"status": "ok"`, com
   `storage.ok: true` e `llm.call_ok: true` (faz uma chamada mínima ao modelo). Se não, o campo `detail`
   diz o que falta — chave recusada, modelo indisponível, conta sem créditos ou banco não configurado.
2. Abra a raiz do site, clique **A** (Beta) → **Simular recebimento** → **Analisar →**.
   A leitura pela IA leva de 10 a 30 segundos.
3. Se a análise falhar, a mensagem na tela já diz a causa (com **Detalhes técnicos** ligado, aparece também o erro bruto).
   O motivo mais comum é o modelo em `OPENAI_MODEL` não estar disponível na sua conta: troque o modelo e faça Redeploy.

## 5. Link público

Em **Settings → Deployment Protection**, deixe **Vercel Authentication desligado** para Production.
O briefing exige que qualquer pessoa abra o link sem login.
