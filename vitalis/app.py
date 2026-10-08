"""API HTTP (FastAPI) — mesmas rotas e formatos do protótipo (handoff §3). Erros no envelope D20."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from . import __version__
from .config import ROOT_DIR, Settings, get_settings
from .dataset import load_examples
from .extraction import Extractor, OpenAIAgentExtractor
from .service import ApiError, Service
from .store import SessionStore, StoreUnavailable, build_store

log = logging.getLogger("vitalis")
HEADER_SESSION = "X-Vitalis-Session"


def create_app(store: SessionStore | None = None, extractor_factory: Callable[[], Extractor] | None = None,
               settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Vitalis", version=__version__,
                  description="Case técnico demonstrativo com dados fictícios. ERP, aprovadores e ingestão de e-mail são simulados; a leitura do e-mail é feita por agente de IA.")

    def default_extractor() -> Extractor:
        if not settings.openai_api_key:
            raise ApiError(503, "LLM_NOT_CONFIGURED", "A leitura por IA não está configurada no servidor (OPENAI_API_KEY).")
        return OpenAIAgentExtractor(settings.openai_api_key, settings.openai_model, settings.openai_timeout_s,
                                    settings.openai_max_retries, settings.openai_reasoning_effort)

    state: dict[str, Any] = {"service": None}

    def svc() -> Service:
        if store is None and settings.on_vercel and settings.storage_backend != "supabase":
            # Em serverless, memória não persiste entre instâncias: a sessão "some" no meio do fluxo.
            raise ApiError(503, "STORAGE_NOT_CONFIGURED",
                           "Persistência não configurada no servidor: defina SUPABASE_URL e SUPABASE_SECRET_KEY na Vercel e faça Redeploy.")
        if state["service"] is None:
            state["service"] = Service(store or build_store(settings), settings, extractor_factory or default_extractor)
        return state["service"]

    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True,
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type", HEADER_SESSION], expose_headers=[HEADER_SESSION])

    def corr(request: Request) -> str:
        return request.headers.get("X-Correlation-Id") or "cor-" + uuid.uuid4().hex[:8]

    def error(request: Request, status: int, code: str, message: str, retryable: bool = False, findings: list | None = None, **extra) -> JSONResponse:
        return JSONResponse(status_code=status, content={"status": status, "code": code, "message": message, "retryable": retryable,
                                                         "findings": findings or [], "correlation_id": corr(request), **extra})

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return error(request, exc.status, exc.code, exc.message, exc.retryable, exc.findings, **exc.extra)

    @app.exception_handler(StoreUnavailable)
    async def store_error(request: Request, exc: StoreUnavailable):
        log.error("store unavailable: %s", exc)
        return error(request, 503, "STORAGE_UNAVAILABLE", "Persistência indisponível no momento. Nada foi alterado; tente de novo.", True)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("erro inesperado")
        return error(request, 500, "INTERNAL_ERROR", "Erro inesperado no servidor. Nenhuma operação foi confirmada.", True)

    def session_id(request: Request) -> str | None:
        h = request.headers.get(HEADER_SESSION)
        return request.cookies.get(settings.session_cookie_name) or (h if h and h != "new" else None)

    def respond(request: Request, sid: str, body: dict) -> JSONResponse:
        resp = JSONResponse(body, headers={"Cache-Control": "no-store"})
        resp.set_cookie(settings.session_cookie_name, sid, httponly=True, secure=settings.session_cookie_secure,
                        samesite="lax", max_age=60 * 60 * 24 * 7, path="/")
        if request.headers.get(HEADER_SESSION) is not None:
            resp.headers[HEADER_SESSION] = sid
        return resp

    async def body_of(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            raise ApiError(422, "INVALID_REQUEST", "Corpo da requisição deve ser um objeto JSON.")
        return data

    # ------------------------------------------------------------------ rotas
    @app.get("/api/health/deep")
    def health_deep() -> dict[str, Any]:
        return health(deep=1)

    @app.get("/api/health")
    def health(deep: int = 0) -> dict[str, Any]:
        """Diagnóstico para o deploy: confere banco e chave de IA sem expor segredos.
        `?deep=1` faz uma chamada mínima ao modelo configurado (custo desprezível) para provar chave e modelo."""
        st = store or (build_store(settings) if settings.storage_backend == "supabase" and settings.supabase_url and settings.supabase_secret_key else None)
        if settings.storage_backend == "supabase" and st is None:
            storage_ok, storage_msg = False, "SUPABASE_URL ou SUPABASE_SECRET_KEY ausente"
        elif hasattr(st, "ping"):
            storage_ok, storage_msg = st.ping()
        else:
            storage_ok, storage_msg = True, "memória (somente desenvolvimento — não persiste entre requisições na Vercel)"
        if settings.on_vercel and settings.storage_backend != "supabase":
            storage_ok, storage_msg = False, "memória não funciona na Vercel — defina SUPABASE_URL e SUPABASE_SECRET_KEY e faça Redeploy"
        llm_ok = bool(settings.openai_api_key)
        llm: dict[str, Any] = {"configured": llm_ok, "model": settings.openai_model, "reasoning_effort": settings.openai_reasoning_effort,
                               "detail": "OPENAI_API_KEY presente" if llm_ok else "OPENAI_API_KEY ausente"}
        if deep and llm_ok:
            import time

            from .extraction import build_chat_model, describe_llm_error

            t0 = time.monotonic()
            try:
                out = build_chat_model(settings.openai_api_key, settings.openai_model, 30, 0, settings.openai_reasoning_effort).invoke(
                    "Responda somente com a palavra: ok")
                llm.update(call_ok=True, reply=str(out.content)[:40])
            except Exception as exc:  # noqa: BLE001 — diagnóstico
                llm_ok = False
                llm.update(call_ok=False, detail=describe_llm_error(exc, settings.openai_model), error=f"{type(exc).__name__}: {str(exc)[:300]}")
            llm["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return {"status": "ok" if storage_ok and llm_ok else "incompleto", "version": __version__,
                "storage": {"backend": settings.storage_backend, "ok": storage_ok, "detail": storage_msg},
                "llm": llm, "runtime": "vercel" if settings.on_vercel else "local",
                "business_date": settings.business_date.isoformat(), "environment": settings.environment}

    @app.get("/api/examples")
    def examples() -> dict[str, Any]:
        """Fixtures originais do dataset (lidas dos .eml), no formato que a interface usa para pré-preencher o formulário."""
        out = {}
        for e in load_examples().values():
            out[e.example_id] = {"file": e.file_name, "message_id": e.message_id, "in_reply_to": e.in_reply_to, "date": e.date_header,
                                 "from": e.sender, "to": e.to, "cc": e.cc, "subject": e.subject, "body": e.body,
                                 "attachment": {"name": e.attachment.filename, "content": e.attachment.content} if e.attachment else None}
        return out

    @app.get("/api/state")
    async def get_state(request: Request):
        sid, view = await run_in_threadpool(svc().state, session_id(request))
        return respond(request, sid, view)

    routes = {
        "/api/inbox": lambda s, sid, b: s.receive(sid, b),
        "/api/inbox/close": lambda s, sid, b: s.close_item(sid),
        "/api/analyze": lambda s, sid, b: s.analyze(sid, b),
        "/api/review": lambda s, sid, b: s.review(sid, b),
        "/api/interpretation/close": lambda s, sid, b: s.close_interpretation(sid, b),
        "/api/rules/close": lambda s, sid, b: s.close_rules(sid, b),
        "/api/approve": lambda s, sid, b: s.approve(sid, b),
        "/api/erp/register": lambda s, sid, b: s.register(sid, b),
        "/api/test-mode": lambda s, sid, b: s.test_mode(sid, b),
        "/api/reset": lambda s, sid, b: s.reset(sid),
    }

    def make(path: str, fn):
        async def handler(request: Request):
            b = await body_of(request)
            sid, view = await run_in_threadpool(fn, svc(), session_id(request), b)
            return respond(request, sid, view)
        handler.__name__ = "post_" + path.strip("/").replace("/", "_").replace("-", "_")
        app.post(path)(handler)

    for path, fn in routes.items():
        make(path, fn)

    # Somente desenvolvimento local (SERVE_PUBLIC=1). Na Vercel, public/ é servido pela CDN.
    public_dir = ROOT_DIR / "public"
    if os.environ.get("SERVE_PUBLIC") == "1" and public_dir.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=public_dir, html=True), name="public")

    return app
