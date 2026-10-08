"""Gera public/ a partir do export do Claude Design (frontend/prototype.html).

O export é um "bundle" único (manifesto base64+gzip + template). Este script:
  1. extrai runtime, React e fontes para public/assets (sem depender de CDN em tempo de execução);
  2. remove o backend simulado no navegador e o dataset embutido (o dataset embutido tinha textos alterados);
  3. liga a interface à API real (USE_MOCK=false; exemplos lidos de /api/examples, isto é, dos .eml originais);
  4. aplica as correções de alinhamento registradas em docs/AUDITORIA_PROTOTIPO.md.

Uso:  python scripts/build_frontend.py [caminho_do_export.html]
Cada substituição é verificada: se o protótipo mudar e um trecho esperado sumir, o build falha em vez de gerar algo silenciosamente errado.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "frontend" / "prototype.html"
OUT = ROOT / "public"
ASSETS = OUT / "assets"


def section(html: str, kind: str) -> str:
    m = re.search(r'<script type="' + re.escape(kind) + r'">(.*?)</script>', html, re.S)
    if not m:
        raise SystemExit(f"Bloco {kind} não encontrado no export.")
    return m.group(1)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"[build] trecho esperado para '{label}' encontrado {n} vez(es); revise o script para o novo export.")
    return text.replace(old, new)


def main() -> None:
    html = SRC.read_text(encoding="utf-8")
    manifest = json.loads(section(html, "__bundler/manifest"))
    template = json.loads(section(html, "__bundler/template"))
    ext = {e["uuid"]: e["id"] for e in json.loads(section(html, "__bundler/ext_resources"))}

    if OUT.exists():
        shutil.rmtree(OUT)
    (ASSETS / "fonts").mkdir(parents=True)

    names: dict[str, str] = {}
    runtime_uuid = None
    for uid, meta in manifest.items():
        raw = base64.b64decode(meta["data"])
        if meta.get("compressed"):
            raw = gzip.decompress(raw)
        mime = meta["mime"]
        if mime.startswith("font/"):
            rel = f"assets/fonts/{uid}.woff2"
        elif uid in ext:
            rel = "assets/" + ext[uid].rsplit("/", 1)[-1]  # react.production.min.js / react-dom.production.min.js
        else:
            text = raw.decode("utf-8", "replace")
            if "dc-runtime" in text[:200]:
                rel, runtime_uuid = "assets/dc-runtime.js", uid
            elif "window.CC_DATASET" in text[:400] or "backend SIMULADO" in text[:400]:
                names[uid] = ""  # descartado
                continue
            else:
                rel = f"assets/{uid}.js"
        (OUT / rel).write_bytes(raw)
        names[uid] = rel
    if not runtime_uuid:
        raise SystemExit("Runtime do Claude Design não encontrado no export.")

    t = template
    # 1) runtime local + React servido pelo próprio site (mapa de recursos lido pelo runtime)
    resources = {url: "/" + names[uid] for uid, url in ext.items()}
    t = replace_once(t, f'<script src="{runtime_uuid}"></script>',
                     "<title>Central de Condições Comerciais</title>\n"
                     '<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 32 32%22%3E%3Crect width=%2232%22 height=%2232%22 rx=%226%22 fill=%22%231F4D46%22/%3E%3Ctext x=%2216%22 y=%2221%22 font-family=%22monospace%22 font-size=%2213%22 font-weight=%22700%22 text-anchor=%22middle%22 fill=%22white%22%3ECC%3C/text%3E%3C/svg%3E">\n'
                     '<meta name="description" content="Cadastro assistido de condições comerciais com fornecedores — demonstração com dados fictícios.">\n'
                     f"<script>window.__resources = {json.dumps(resources)};</script>\n"
                     '<script src="/assets/dc-runtime.js"></script>', "runtime")
    # 2) remove backend simulado, dataset embutido e notas de handoff
    for uid, rel in names.items():
        if rel == "":
            t = replace_once(t, f'<script src="{uid}"></script>', "", f"script descartado {uid}")
    t = re.sub(r'<script type="text/markdown" id="handoff">.*?</script>', "", t, flags=re.S)
    # fontes
    for uid, rel in names.items():
        if rel.startswith("assets/fonts/"):
            t = t.replace(f'url("{uid}")', f'url("/{rel}")')
    if re.search(r'url\("[0-9a-f-]{36}"\)', t):
        raise SystemExit("Restou referência de recurso não resolvida no template.")

    # 3) ligação com a API real
    t = replace_once(t, "static USE_MOCK = true;", "static USE_MOCK = false;", "USE_MOCK")
    t = replace_once(t, "fetch('dataset/fixtures.json')", "fetch('/api/examples', { credentials: 'include' })", "fixtures")
    t = replace_once(t, "backendLabel: C.USE_MOCK ? 'backend simulado no navegador (sem IA)' : 'backend conectado',",
                     "backendLabel: C.USE_MOCK ? 'backend simulado no navegador (sem IA)' : 'backend conectado · leitura por agente de IA',", "backendLabel")

    # 4) correções de alinhamento (docs/AUDITORIA_PROTOTIPO.md)
    t = replace_once(t, "<li>Leitura por modelo de IA nesta versão da interface: a extração é simulada por regras sobre o texto</li>",
                     "<li>Garantia de acerto da leitura por IA: todo dado incerto passa pela sua verificação antes das regras</li>",
                     "escopo: IA")
    t = replace_once(t, "<li>E-mail em texto livre, com ou sem planilha anexa, como entrada</li>",
                     "<li>E-mail em texto livre, com ou sem planilha anexa, como entrada</li>"
                     "<li>Leitura do e-mail por agente de IA, com evidência do trecho de origem de cada dado</li>",
                     "escopo: dentro")
    t = replace_once(t, '<sc-if value="{{ hasNotice }}" hint-placeholder-val="{{ false }}">',
                     '<sc-if value="{{ isAnalyzing }}" hint-placeholder-val="{{ false }}">\n'
                     '<div role="status" style="padding:12px 16px;border:1px solid #CBD7E2;background:#E8EEF3;color:#3D5A73;border-radius:8px;'
                     "font:500 13.5px/1.45 'IBM Plex Sans'\">A IA está lendo a mensagem e o anexo — costuma levar de 10 a 30 segundos. "
                     "Nada é gravado sem a sua revisão.</div>\n</sc-if>\n"
                     '<sc-if value="{{ hasNotice }}" hint-placeholder-val="{{ false }}">', "aviso de leitura")
    t = replace_once(t, "onDismissError: () => this.setState({ error: null }), hasNotice: !!s.notice, notice: s.notice,",
                     "onDismissError: () => this.setState({ error: null }), hasNotice: !!s.notice, notice: s.notice, isAnalyzing: s.busy === 'analyze',",
                     "isAnalyzing")

    (OUT / "index.html").write_text(t, encoding="utf-8")
    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"public/ gerado: {len(list(OUT.rglob('*')))} itens, {total / 1024:.0f} KB")


if __name__ == "__main__":
    main()
