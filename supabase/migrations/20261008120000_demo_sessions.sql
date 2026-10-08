-- Vitalis — estado mutável por sessão demonstrativa (ADR-05, §7.5).
-- Uma única tabela JSONB, desnormalizada de propósito: adequada ao dataset fictício,
-- NÃO é o modelo recomendado para o ERP de uma organização real.
-- Os dados originais (EML, CSV, snapshots do ERP, política) ficam versionados no repositório
-- (ADR-07); cada sessão nasce com uma cópia do snapshot de 30/09/2026 criada pelo backend.

create table if not exists public.demo_sessions (
  id          uuid        primary key default gen_random_uuid(),
  state       jsonb       not null,
  version     integer     not null default 1,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  constraint demo_sessions_version_positive check (version >= 1),
  constraint demo_sessions_state_is_object check (jsonb_typeof(state) = 'object'),
  -- teto de 2 MB por sessão (controle de custo/abuso do link público — D18)
  constraint demo_sessions_state_size check (octet_length(state::text) <= 2097152)
);

comment on table public.demo_sessions is
  'Estado isolado por visitante da demonstração Vitalis (análises, aprovações simuladas, ERP simulado). Dados fictícios.';
comment on column public.demo_sessions.version is
  'Controle otimista (D15): toda gravação exige a versão observada e incrementa exatamente 1.';

create index if not exists demo_sessions_updated_at_idx on public.demo_sessions (updated_at);

-- Invariantes de gravação: updated_at automático e versão estritamente sequencial.
create or replace function public.demo_sessions_before_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.version <> old.version + 1 then
    raise exception 'demo_sessions: versão deve avançar de % para %, recebida %', old.version, old.version + 1, new.version
      using errcode = 'check_violation';
  end if;
  if new.id <> old.id or new.created_at <> old.created_at then
    raise exception 'demo_sessions: id e created_at são imutáveis' using errcode = 'check_violation';
  end if;
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists demo_sessions_before_update on public.demo_sessions;
create trigger demo_sessions_before_update
  before update on public.demo_sessions
  for each row execute function public.demo_sessions_before_update();

-- Acesso: somente o backend (chave secreta / service_role, que ignora RLS).
-- RLS ligado e SEM políticas => anon e authenticated não leem nem escrevem.
alter table public.demo_sessions enable row level security;
alter table public.demo_sessions force row level security;
revoke all on table public.demo_sessions from anon, authenticated;
grant select, insert, update, delete on table public.demo_sessions to service_role;

-- Retenção (D18): remove sessões inativas. Executável apenas pelo backend/operador.
create or replace function public.purge_stale_demo_sessions(p_older_than interval default interval '7 days')
returns integer
language sql
security invoker
set search_path = ''
as $$
  with deleted as (
    delete from public.demo_sessions
    where updated_at < now() - p_older_than
    returning 1
  )
  select count(*)::integer from deleted;
$$;

revoke execute on function public.purge_stale_demo_sessions(interval) from public, anon, authenticated;
grant execute on function public.purge_stale_demo_sessions(interval) to service_role;
revoke execute on function public.demo_sessions_before_update() from public, anon, authenticated;
