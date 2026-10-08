"""Configuração lida exclusivamente de variáveis de ambiente (segredos só no servidor)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATASET_DIR = ROOT_DIR / "data" / "dataset"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip().strip('"').strip("'").strip()  # tolera aspas coladas junto com o valor no painel
    return value or default


def normalize_supabase_url(raw: str | None) -> str | None:
    """Aceita a URL com ou sem https://, com /rest/v1 no fim, ou só o ID do projeto."""
    if not raw:
        return None
    u = raw.strip().rstrip("/")
    if u.startswith(("postgres://", "postgresql://")):  # string de conexão do banco colada no lugar da URL da API
        import re

        m = re.search(r"(?:postgres\.|db\.)([a-z0-9]{20})", u)
        return f"https://{m.group(1)}.supabase.co" if m else None
    if u.endswith("/rest/v1"):
        u = u[: -len("/rest/v1")]
    if "://" not in u:
        u = "https://" + (u if "." in u else f"{u}.supabase.co")
    return u.rstrip("/")


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw is not None else default


def _env_list(name: str) -> list[str]:
    raw = _env(name, "") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # Persistência (Supabase PostgREST). Sem URL/chave => armazenamento em memória (somente dev/testes).
    supabase_url: str | None = None
    supabase_secret_key: str | None = None
    storage_backend: str = "memory"  # "supabase" | "memory"

    # IA (D23): modelo configurável; gpt-5-mini é candidato, não decisão congelada.
    openai_api_key: str | None = None
    openai_model: str = "gpt-5-mini"
    openai_timeout_s: int = 90
    openai_max_retries: int = 1
    # Modelos de raciocínio (gpt-5*, o*): "low" reduz a latência sem mudar o contrato de saída.
    openai_reasoning_effort: str | None = "low"

    # Data de negócio simulada (D04).
    business_date: date = date(2026, 9, 30)

    # Limites de uso (D18 — princípio aprovado; números ajustáveis por ambiente).
    max_analyses_per_session: int = 25
    max_body_chars: int = 8_000
    max_subject_chars: int = 300
    max_sender_chars: int = 320
    max_attachment_bytes: int = 204_800
    max_analyses_kept: int = 40

    # Sessão (D17): cookie opaco emitido pelo backend.
    session_cookie_name: str = "vitalis_sid"
    session_cookie_secure: bool = True
    allowed_origins: list[str] = field(default_factory=list)

    environment: str = "development"
    on_vercel: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Aceita os nomes usados pela integração Supabase ↔ Vercel, além dos do .env.example.
    supabase_url = _env("SUPABASE_URL") or _env("NEXT_PUBLIC_SUPABASE_URL")
    supabase_key = (_env("SUPABASE_SECRET_KEY") or _env("SUPABASE_SERVICE_ROLE_KEY") or _env("SUPABASE_SERVICE_KEY")
                    or _env("SUPABASE_KEY"))
    backend = (_env("STORAGE_BACKEND") or "").lower() or ("supabase" if supabase_url and supabase_key else "memory")
    if backend not in ("supabase", "memory"):
        backend = "supabase" if supabase_url and supabase_key else "memory"
    environment = _env("APP_ENV", "development") or "development"
    bd_raw = _env("BUSINESS_DATE", "2026-09-30") or "2026-09-30"
    return Settings(
        supabase_url=normalize_supabase_url(supabase_url),
        supabase_secret_key=supabase_key,
        storage_backend=backend,
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_model=_env("OPENAI_MODEL", "gpt-5-mini") or "gpt-5-mini",
        openai_timeout_s=_env_int("OPENAI_TIMEOUT_S", 90),
        openai_max_retries=_env_int("OPENAI_MAX_RETRIES", 1),
        openai_reasoning_effort=(_env("OPENAI_REASONING_EFFORT", "low") or "low").lower(),
        business_date=date.fromisoformat(bd_raw),
        max_analyses_per_session=_env_int("MAX_ANALYSES_PER_SESSION", 25),
        max_body_chars=_env_int("MAX_BODY_CHARS", 8_000),
        max_attachment_bytes=_env_int("MAX_ATTACHMENT_BYTES", 204_800),
        session_cookie_secure=(_env("SESSION_COOKIE_SECURE", "true") or "true").lower() != "false",
        allowed_origins=_env_list("ALLOWED_ORIGINS"),
        environment=environment,
        on_vercel=bool(_env("VERCEL")),
    )
