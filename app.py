"""Entrypoint detectado pela Vercel (instância FastAPI chamada `app`)."""

from vitalis.app import create_app

app = create_app()
