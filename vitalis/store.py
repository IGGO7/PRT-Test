"""Persistência do estado por sessão (`demo_sessions`) com controle otimista de versão (D15).

A gravação só é aceita se a versão observada ainda for a atual (compare-and-set). Em conflito,
o chamador recebe `VersionConflict` e deve recarregar — nunca sobrescrever nem repetir às cegas.
"""

from __future__ import annotations

import copy
import threading
import uuid
from typing import Any, Protocol

import httpx


class VersionConflict(Exception):
    pass


class SessionNotFound(Exception):
    pass


class StoreUnavailable(Exception):
    pass


class SessionStore(Protocol):
    def create(self, state: dict[str, Any]) -> tuple[str, int]: ...
    def load(self, session_id: str) -> tuple[dict[str, Any], int]: ...
    def save(self, session_id: str, state: dict[str, Any], expected_version: int) -> int: ...
    def replace(self, session_id: str, state: dict[str, Any], expected_version: int) -> int: ...


class MemoryStore:
    """Somente desenvolvimento local e testes. Não atende RNF05 em serverless."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[dict[str, Any], int]] = {}
        self._lock = threading.Lock()

    def create(self, state: dict[str, Any]) -> tuple[str, int]:
        sid = str(uuid.uuid4())
        with self._lock:
            self._rows[sid] = (copy.deepcopy(state), 1)
        return sid, 1

    def load(self, session_id: str) -> tuple[dict[str, Any], int]:
        with self._lock:
            if session_id not in self._rows:
                raise SessionNotFound(session_id)
            state, version = self._rows[session_id]
            return copy.deepcopy(state), version

    def save(self, session_id: str, state: dict[str, Any], expected_version: int) -> int:
        with self._lock:
            if session_id not in self._rows:
                raise SessionNotFound(session_id)
            _, current = self._rows[session_id]
            if current != expected_version:
                raise VersionConflict(f"versão atual {current}, esperada {expected_version}")
            self._rows[session_id] = (copy.deepcopy(state), current + 1)
            return current + 1

    replace = save


class SupabaseStore:
    """Acesso via PostgREST com a chave secreta (somente servidor). RLS ativo e sem políticas:
    anon/authenticated não leem nem escrevem a tabela."""

    def __init__(self, url: str, secret_key: str, timeout_s: float = 8.0):
        self.base = f"{url}/rest/v1/demo_sessions"
        headers = {"apikey": secret_key, "Content-Type": "application/json", "Accept": "application/json"}
        if secret_key.startswith("eyJ"):  # chave legada service_role (JWT)
            headers["Authorization"] = f"Bearer {secret_key}"
        self.client = httpx.Client(headers=headers, timeout=timeout_s)

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            resp = self.client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise StoreUnavailable(f"Falha ao acessar o banco: {type(exc).__name__}") from exc
        if resp.status_code >= 500 or resp.status_code in (401, 403):
            raise StoreUnavailable(f"Banco respondeu {resp.status_code}: {resp.text[:200]}")
        return resp

    def create(self, state: dict[str, Any]) -> tuple[str, int]:
        resp = self._request("POST", self.base, json={"state": state, "version": 1},
                             headers={"Prefer": "return=representation"}, params={"select": "id,version"})
        if resp.status_code not in (200, 201):
            raise StoreUnavailable(f"Não foi possível criar a sessão: {resp.status_code} {resp.text[:200]}")
        row = resp.json()[0]
        return row["id"], row["version"]

    def load(self, session_id: str) -> tuple[dict[str, Any], int]:
        try:
            uuid.UUID(session_id)
        except ValueError as exc:
            raise SessionNotFound(session_id) from exc
        resp = self._request("GET", self.base, params={"id": f"eq.{session_id}", "select": "state,version"})
        rows = resp.json() if resp.status_code == 200 else []
        if not rows:
            raise SessionNotFound(session_id)
        return rows[0]["state"], rows[0]["version"]

    def save(self, session_id: str, state: dict[str, Any], expected_version: int) -> int:
        resp = self._request(
            "PATCH", self.base,
            params={"id": f"eq.{session_id}", "version": f"eq.{expected_version}", "select": "version"},
            json={"state": state, "version": expected_version + 1},
            headers={"Prefer": "return=representation"},
        )
        if resp.status_code != 200:
            raise StoreUnavailable(f"Falha ao gravar a sessão: {resp.status_code} {resp.text[:200]}")
        rows = resp.json()
        if not rows:
            raise VersionConflict(f"A sessão foi alterada por outra operação (versão esperada {expected_version}).")
        return rows[0]["version"]

    replace = save

    def ping(self) -> tuple[bool, str]:
        """Confere se a tabela existe e se a chave tem acesso (sem gravar nada)."""
        try:
            resp = self.client.get(self.base, params={"select": "id", "limit": "1"})
        except httpx.UnsupportedProtocol:
            return False, "SUPABASE_URL inválida — use o formato https://SEU-PROJETO.supabase.co"
        except httpx.HTTPError as exc:
            host = httpx.URL(self.base).host
            return False, (f"sem conexão com o Supabase em {host} ({type(exc).__name__}) — confira SUPABASE_URL: "
                           "deve ser https://<ID-do-projeto>.supabase.co (Supabase → Project Settings → API)")
        if resp.status_code == 200:
            return True, "tabela demo_sessions acessível"
        if resp.status_code == 404 or "PGRST205" in resp.text or "does not exist" in resp.text:
            return False, "tabela demo_sessions não existe — rode supabase/migrations/20261008120000_demo_sessions.sql no SQL Editor"
        if resp.status_code in (401, 403) or "42501" in resp.text:
            return False, "chave sem permissão — use a SECRET key (sb_secret_...) em SUPABASE_SECRET_KEY, não a publishable"
        return False, f"Supabase respondeu {resp.status_code}: {resp.text[:160]}"


_memory_singleton: MemoryStore | None = None


def build_store(settings) -> SessionStore:
    global _memory_singleton
    if settings.storage_backend == "supabase":
        if not (settings.supabase_url and settings.supabase_secret_key):
            raise RuntimeError("STORAGE_BACKEND=supabase exige SUPABASE_URL e SUPABASE_SECRET_KEY.")
        return SupabaseStore(settings.supabase_url, settings.supabase_secret_key)
    if _memory_singleton is None:
        _memory_singleton = MemoryStore()
    return _memory_singleton
