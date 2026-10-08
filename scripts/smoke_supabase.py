"""Marco 0/B — prova de persistência: cria sessão, lê, grava com CAS, força conflito e limpa.

Uso:  python scripts/smoke_supabase.py      (lê SUPABASE_URL e SUPABASE_SECRET_KEY do ambiente/.env)
"""

from __future__ import annotations

from _env import load_env

load_env()

from vitalis.config import get_settings  # noqa: E402
from vitalis.store import SupabaseStore, VersionConflict  # noqa: E402
from vitalis.service import new_state  # noqa: E402


def main() -> None:
    s = get_settings()
    if not (s.supabase_url and s.supabase_secret_key):
        raise SystemExit("Defina SUPABASE_URL e SUPABASE_SECRET_KEY.")
    store = SupabaseStore(s.supabase_url, s.supabase_secret_key)
    state = new_state(s)
    sid, v = store.create(state)
    print(f"criada sessão {sid} v{v} ({len(state['erp']['condicoes']['itens'])} condições vigentes no snapshot)")
    loaded, v = store.load(sid)
    assert loaded["erp"]["next_seq"] == 11, loaded["erp"]["next_seq"]
    loaded["counters"]["resets"] = 99
    v2 = store.save(sid, loaded, v)
    print(f"gravada v{v2}")
    try:
        store.save(sid, loaded, v)  # versão antiga => deve falhar
        raise SystemExit("ERRO: gravação com versão obsoleta foi aceita")
    except VersionConflict:
        print("conflito de versão detectado corretamente (D15)")
    resp = store.client.delete(store.base, params={"id": f"eq.{sid}"})
    print(f"sessão de teste removida (HTTP {resp.status_code}). OK")


if __name__ == "__main__":
    main()
