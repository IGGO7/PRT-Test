"""Servidor local para testar a interface SEM chamar a OpenAI (usa o dublê de extração dos testes).

Uso:  SERVE_PUBLIC=1 python -m uvicorn tests.ui_harness:app --port 8020
Não é usado em produção: o app publicado (app.py) sempre usa o agente de IA real.
"""

from datetime import date

from tests.conftest import FakeExtractor
from vitalis.app import create_app
from vitalis.config import Settings
from vitalis.store import MemoryStore

_fake = FakeExtractor()
app = create_app(store=MemoryStore(), extractor_factory=lambda: _fake,
                 settings=Settings(storage_backend="memory", session_cookie_secure=False, business_date=date(2026, 9, 30)))
