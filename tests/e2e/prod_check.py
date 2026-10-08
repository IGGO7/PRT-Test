"""Verificação ponta a ponta no site PUBLICADO, com o agente de IA real (sem dublês).

Cada cenário usa uma sessão nova e percorre o fluxo pela mesma API que a interface chama.
Além dos 4 e-mails do dataset, envia variações do texto (percentual, datas, campanha,
instrução embutida) e e-mails inéditos, para provar que dados, sinais e regras mudam
com o conteúdo da mensagem — nada é pré-calculado por cenário.

Uso:  BASE_URL=https://seu-site.vercel.app python tests/e2e/prod_check.py [--out relatorio]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

BASE = os.environ.get("BASE_URL", "").rstrip("/")
ROOT = Path(__file__).resolve().parents[2]
EMAILS = ROOT / "data" / "dataset" / "emails"


class Flow:
    def __init__(self, name: str):
        self.name = name
        self.c = httpx.Client(base_url=BASE, timeout=310, follow_redirects=True)
        self.v = self.get("/api/state")
        self.checks: list[dict] = []
        self.calls = 0

    def get(self, path):
        r = self.c.get(path)
        r.raise_for_status()
        return r.json()

    def post(self, path, body=None, expect=200):
        t0 = time.monotonic()
        r = self.c.post(path, json=body or {})
        self.calls += 1
        try:
            j = r.json()
        except ValueError:
            j = {"raw": r.text[:300]}
        if r.status_code != expect:
            raise AssertionError(f"{path} → HTTP {r.status_code} (esperado {expect}): {json.dumps(j, ensure_ascii=False)[:600]}")
        if r.status_code == 200:
            self.v = j
        j["_secs"] = round(time.monotonic() - t0, 1)
        return j

    # ---------------------------------------------------------------- atalhos
    @property
    def p(self):
        return self.v["proposal"]

    def f(self, key):
        return next((x for x in self.p["fields"] if x["key"] == key), None)

    def val(self, key):
        f = self.f(key)
        return f and f["value"]

    def ids(self):
        return {x["id"] for x in self.p["findings"]}

    def check(self, label, ok, got=None):
        self.checks.append({"check": label, "ok": bool(ok), "got": got})
        return ok

    def receive_example(self, ex):
        self.post("/api/inbox", {"mode": "dataset_example", "example_id": ex})
        return self.v["inbox"][0]["id"]

    def receive_text(self, sender, subject, body, csv=None, derived=None):
        self.post("/api/inbox", {"mode": "manual_simulation", "sender": sender, "subject": subject, "body": body,
                                 "csv": csv, "csv_name": "anexo.csv" if csv else None, "derived_from_example": derived})
        return self.v["inbox"][0]["id"]

    def analyze(self, item):
        r = self.post("/api/analyze", {"item_id": item})
        self.analysis_secs = r["_secs"]
        p = self.p
        self.check("leitura feita pelo agente de IA real (não dublê)", p["extractor"].startswith("Agente de IA") and "gpt" in p["extractor"], p["extractor"])
        meta = p.get("extraction_meta") or {}
        self.check("metadados da chamada ao modelo registrados", meta.get("engine") == "langchain.create_agent" and meta.get("latency_ms", 0) > 300,
                   {k: meta.get(k) for k in ("engine", "model", "latency_ms")})
        return p

    def close_interp(self, changes=None, absent=None):
        pend = [f["key"] for f in self.p["fields"] if f["confirmation_status"] == "PENDING" and f["value"] not in (None, [])
                and f["key"] not in (changes or {})]
        return self.post("/api/interpretation/close", {"proposal_id": self.p["id"], "expected_version": self.v["version"],
                                                       "changes": changes or {}, "confirmations": pend, "absent": absent or []})

    def ack_all(self):
        ids = [f["id"] for f in self.p["findings"] if f["severity"] == "WARNING"]
        if ids:
            self.post("/api/review", {"proposal_id": self.p["id"], "expected_version": self.v["version"], "acknowledged_warning_ids": ids})

    def close_rules(self):
        return self.post("/api/rules/close", {"proposal_id": self.p["id"], "expected_version": self.v["version"], "apply_auto": True})

    def approve(self, who, expect=200):
        return self.post("/api/approve", {"proposal_id": self.p["id"], "revision_hash": self.p["revision_hash"],
                                          "expected_version": self.v["version"], "approver_id": who}, expect)

    def register(self):
        return self.post("/api/erp/register", {"proposal_id": self.p["id"], "approval_id": self.v["approval"]["approval_id"],
                                               "expected_version": self.v["version"]})

    def missing_empty(self):
        return [f["key"] for f in self.p["fields"] if f["confirmation_status"] == "PENDING" and f["value"] in (None, [])]

    def snapshot(self):
        p = self.p or {}
        return {
            "fields": [{"campo": f["key"], "valor": f["value"], "origem": f["origin_kind"], "status": f["confirmation_status"],
                        "fonte": f.get("source_location"), "trecho": f.get("raw_value")} for f in p.get("fields", [])],
            "evidencias": [e["text"] for e in p.get("evidence", [])],
            "sinais": p.get("flags"), "origem_sinais": p.get("flag_sources"),
            "resumo_agente": p.get("ai_summary"), "ambiguidades": p.get("ambiguities"),
            "apontamentos": [f"{f['severity']} {f['id']}" for f in p.get("findings", [])],
            "estado": p.get("state"), "decisao": p.get("decision") and {k: p["decision"].get(k) for k in ("type", "route", "rule", "automatic")},
            "auto": (p.get("conclusion") or {}).get("auto") and p["conclusion"]["auto"]["rule"],
            "extrator": p.get("extractor"),
        }


def body_of(name):
    raw = (EMAILS / name).read_text(encoding="utf-8")
    head, _, body = raw.partition("\n\n")
    hdr = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
    return hdr["From"], hdr["Subject"], body


BETA = body_of("2026-09-29_1005_beta.eml")
ALFA = body_of("2026-09-28_0912_alfa.eml")
NUTRI = body_of("2026-09-29_1830_nutrivida.eml")


# ---------------------------------------------------------------- cenários do dataset
def sc_beta(t: Flow):
    t.analyze(t.receive_example("beta"))
    t.check("fornecedor = FORN-002 (Beta)", t.val("fornecedor") == "FORN-002", t.val("fornecedor"))
    t.check("categoria = HIGIENE_BELEZA (\"HPC\")", t.val("categoria") == "HIGIENE_BELEZA", t.val("categoria"))
    t.check("desconto = 13% explícito", t.val("desconto") == 13.0 and t.f("desconto")["origin_kind"] == "EXPLICIT", t.f("desconto"))
    t.check("lojas = Tijuca, Méier, Botafogo", sorted(t.val("lojas") or []) == ["LOJA-104", "LOJA-107", "LOJA-112"], t.val("lojas"))
    t.check("vigência 01/10/2026–31/03/2027", (t.val("inicio"), t.val("fim")) == ("2026-10-01", "2027-03-31"), (t.val("inicio"), t.val("fim")))
    t.check("evidência literal do desconto marcada na fonte", any("13%" in e["text"] for e in t.p["evidence"]), [e["text"] for e in t.p["evidence"]])
    t.check("sem sinais de risco", not (t.p["flags"].get("injection") or t.p["flags"].get("approval_claim")), t.p["flags"])
    t.close_interp()
    t.check("RB10 substituição de COND-003 + D05 divergência", {"RB10:COND-003", "D05:COND-003"} <= t.ids(), sorted(t.ids()))
    t.ack_all()
    t.close_rules()
    t.check("pronta para aprovação, alçada Gerência", t.p["state"] == "PRONTA_PARA_APROVACAO" and t.p["required"]["level"] == 2, (t.p["state"], t.p["required"]))
    t.approve("coord", expect=403)
    t.check("Coordenação recusada por alçada insuficiente", True)
    t.approve("ger")
    t.register()
    op = t.v["operation"] or {}
    cond = next((c for c in t.v.get("erp_condicoes", []) if c.get("cod_condicao") == "COND-003"), {})
    t.check("cadastrada no ERP simulado", t.p["state"] == "CADASTRADA" and op.get("confirmation_status") == "CONFIRMED", (t.p["state"], op.get("confirmation_status")))
    t.check("COND-003 encerrada em 30/09/2026", cond.get("data_fim") == "2026-09-30", {k: cond.get(k) for k in ("data_fim", "status", "encerrada_por")})


def sc_gama(t: Flow):
    t.analyze(t.receive_example("gama"))
    d = t.f("desconto")
    t.check("desconto em CONFLITO e-mail 12% × anexo 12,5%", d and d["origin_kind"] == "CONFLICT" and d["value"] is None, d)
    t.check("verba R$ 8.000 (total)", t.val("verba") == 8000.0, t.f("verba"))
    t.check("fim de vigência ausente", t.f("fim")["origin_kind"] == "MISSING", t.f("fim"))
    t.check("categoria DERMOCOSMETICOS (\"linha dermo\")", t.val("categoria") == "DERMOCOSMETICOS", t.val("categoria"))
    t.check("lojas do anexo (Copacabana, Ipanema, Leblon, Barra)", sorted(t.val("lojas") or []) == ["LOJA-101", "LOJA-102", "LOJA-103", "LOJA-109"], t.val("lojas"))
    t.check("células do CSV destacadas", len(t.p.get("csv_highlights") or []) > 0, t.p.get("csv_highlights"))
    t.check("sem falso sinal de campanha sazonal (\"Q4\" é período, não campanha)", not t.p["flags"].get("seasonal"), t.p["flags"].get("seasonal"))
    t.check("sem falso sinal de instrução embutida", not t.p["flags"].get("injection"), t.p["flags"].get("injection"))
    t.close_interp(changes={"desconto": 12.5}, absent=[k for k in t.missing_empty() if k != "desconto"])
    t.check("bloqueios: RB06 componentes (normativo) + RB04 fim", {"RB06:COMPONENTES", "RB04:FIM"} <= t.ids(), sorted(t.ids()))
    t.close_rules()
    opts = {o["code"]: o["available"] for o in t.p["conclusion"]["options"]}
    t.check("não cadastrável; escalonar à Diretoria disponível", not opts["APROVAR_CADASTRAR"] and opts["ESCALAR:DIRETORIA"], opts)


def sc_alfa(t: Flow):
    t.analyze(t.receive_example("alfa"))
    t.check("desconto 28%, GENERICOS, toda a rede", (t.val("desconto"), t.val("categoria"), t.val("lojas")) == (28.0, "GENERICOS", ["REDE"]),
            (t.val("desconto"), t.val("categoria"), t.val("lojas")))
    t.check("campanha sazonal identificada", bool(t.p["flags"].get("seasonal")), (t.p["flags"].get("seasonal"), (t.p.get("flag_sources") or {}).get("seasonal")))
    t.close_interp(absent=t.missing_empty())
    t.check("RB13 exceção sazonal (normativo), sem RB02", "RB13:SAZONAL" in t.ids() and "RB02:TETO" not in t.ids(), sorted(t.ids()))
    t.close_rules()
    dec = t.p.get("decision") or {}
    t.check("encaminhada automaticamente à Diretoria (AUTO-04)", dec.get("rule") == "AUTO-04" and dec.get("route") == "DIRETORIA", dec)
    t.approve("dir", expect=409)
    t.check("aprovação impedida", True)


def sc_nutrivida(t: Flow):
    t.analyze(t.receive_example("nutrivida"))
    t.check("fornecedor FORN-005 (inativo), SUPLEMENTOS, 18%", (t.val("fornecedor"), t.val("categoria"), t.val("desconto")) == ("FORN-005", "SUPLEMENTOS", 18.0),
            (t.val("fornecedor"), t.val("categoria"), t.val("desconto")))
    src = t.p.get("flag_sources") or {}
    t.check("instrução embutida detectada", bool(t.p["flags"].get("injection")), (t.p["flags"].get("injection"), src.get("injection")))
    t.check("alegação de pré-aprovação detectada", bool(t.p["flags"].get("approval_claim")), (t.p["flags"].get("approval_claim"), src.get("approval_claim")))
    t.check("agente reportou os sinais de risco por conta própria", "agente" in src.get("injection", "") and "agente" in src.get("approval_claim", ""), src)
    t.close_interp(absent=t.missing_empty())
    t.check("RB01 fornecedor inativo + SEG + RB13 alegação", {"RB01:FORN-005", "SEG:INSTRUCAO", "RB13:ALEGACAO"} <= t.ids(), sorted(t.ids()))
    t.close_rules()
    dec = t.p.get("decision") or {}
    t.check("bloqueada e encaminhada ao Cadastro (AUTO-01)", dec.get("rule") == "AUTO-01" and t.p["state"] == "BLOQUEADA", (t.p["state"], dec))


# ---------------------------------------------------------------- variações: o resultado acompanha o texto
def edited(t: Flow, base, old, new, subject=None):
    sender, subj, body = base
    assert old in body, old
    return t.analyze(t.receive_text(sender, subject or subj, body.replace(old, new), derived="beta" if base is BETA else None))


def sc_beta_9(t: Flow):
    edited(t, BETA, "13% de desconto", "9% de desconto")
    t.check("desconto lido = 9% (texto alterado)", t.val("desconto") == 9.0, t.val("desconto"))
    t.close_interp(absent=t.missing_empty())
    t.check("alçada passa a Coordenação", t.p["required"]["level"] == 1, t.p["required"])
    t.check("origem editada sinalizada (RB07)", "RB07:ORIGEM" in t.ids(), sorted(t.ids()))


def sc_beta_22(t: Flow):
    edited(t, BETA, "13% de desconto", "22% de desconto")
    t.check("desconto lido = 22%", t.val("desconto") == 22.0, t.val("desconto"))
    t.check("as três lojas mantidas", sorted(t.val("lojas") or []) == ["LOJA-104", "LOJA-107", "LOJA-112"], t.val("lojas"))
    t.close_interp(absent=t.missing_empty())
    t.check("RB02 teto de HIGIENE_BELEZA (20%)", "RB02:TETO" in t.ids(), sorted(t.ids()))
    t.close_rules()
    t.check("bloqueio automático por política (AUTO-03)", ((t.p["conclusion"] or {}).get("auto") or {}).get("rule") == "AUTO-03", t.p["conclusion"].get("auto"))


def sc_beta_41(t: Flow):
    edited(t, BETA, "13% de desconto", "41% de desconto")
    t.check("desconto lido = 41%", t.val("desconto") == 41.0, t.f("desconto"))
    t.check("prévia de regra já na interpretação (teto e vedação)", {"RB02:TETO", "RB03:VEDADO"} <= t.ids(), sorted(t.ids()))
    t.close_interp(absent=t.missing_empty())
    t.close_rules()
    t.check("bloqueada automaticamente (AUTO-03)", ((t.p["conclusion"] or {}).get("auto") or {}).get("rule") == "AUTO-03" and t.p["state"] == "BLOQUEADA",
            (t.p["state"], (t.p["conclusion"] or {}).get("auto")))


def sc_beta_data_invalida(t: Flow):
    edited(t, BETA, "até 31/03/2027", "até 31/02/2027")
    f = t.f("fim")
    t.check("31/02/2027 não é corrigida em silêncio", f["value"] is None and "31/02" in (f.get("reason") or ""), f)
    t.close_interp(absent=t.missing_empty())
    msg = next((x["message"] for x in t.p["findings"] if x["id"] == "RB04:FIM"), "")
    t.check("RB04 aponta data de fim inválida", "inválida" in msg and "31/02" in msg, msg)


def sc_beta_retro(t: Flow):
    edited(t, BETA, "valendo de 01/10/2026", "valendo de 01/09/2026")
    t.check("início lido = 01/09/2026", t.val("inicio") == "2026-09-01", t.val("inicio"))
    t.close_interp(absent=t.missing_empty())
    t.check("RB04 início retroativo", "RB04:RETRO" in t.ids(), sorted(t.ids()))


def sc_beta_long(t: Flow):
    edited(t, BETA, "até 31/03/2027", "até 31/12/2027")
    t.check("fim lido = 31/12/2027", t.val("fim") == "2027-12-31", t.val("fim"))
    t.close_interp(absent=t.missing_empty())
    t.check("RB04 vigência > 12 meses", "RB04:DURACAO" in t.ids(), sorted(t.ids()))


def sc_alfa_sem_campanha(t: Flow):
    sender, _, body = ALFA
    body = (body.replace("segue a nossa proposta para a\nCampanha de Fim de Ano da linha de genéricos Alfa.", "segue a nossa proposta para a\nlinha de genéricos Alfa.")
                .replace("Desconto de 28%", "Desconto de 22%")
                .replace("durante toda a campanha", "durante toda a vigência")
                .replace("É a nossa principal campanha sazonal do ano, então contamos muito com o\napoio de vocês pra fechar rápido.", "Contamos com o apoio de vocês."))
    assert "ampanha" not in body and "sazonal" not in body
    t.analyze(t.receive_text(sender, "Proposta genéricos Alfa", body))
    t.check("desconto lido = 22%", t.val("desconto") == 22.0, t.val("desconto"))
    t.check("sem campanha, sem sinal sazonal", not t.p["flags"].get("seasonal"), t.p["flags"].get("seasonal"))
    t.close_interp(absent=t.missing_empty())
    t.check("vira RB02 teto (15%), sem exceção sazonal", "RB02:TETO" in t.ids() and "RB13:SAZONAL" not in t.ids(), sorted(t.ids()))


def sc_nutrivida_sem_nota(t: Flow):
    sender, subj, body = NUTRI
    clean = body[: body.index("[NOTA")] + body[body.index("-----Mensagem original"):]
    t.analyze(t.receive_text(sender, subj, clean))
    t.check("sem a nota: nenhum sinal de instrução ou alegação", not t.p["flags"].get("injection") and not t.p["flags"].get("approval_claim"), t.p["flags"])
    t.check("fornecedor continua FORN-005", t.val("fornecedor") == "FORN-005", t.val("fornecedor"))


def sc_inedito_delta(t: Flow):
    body = ("Olá Renata,\n\nA Delta Laboratórios propõe 8% de desconto na linha de MIP (analgésicos e antigripais) "
            "para as lojas de Copacabana e Ipanema, de 01/11/2026 a 30/04/2027.\n\n"
            "Contrapartida: display de balcão nas duas lojas durante toda a vigência.\n\nAbraços,\nFernanda Dias\nDelta Laboratórios")
    t.analyze(t.receive_text("Fernanda Dias <fernanda.dias@deltalab.com.br>", "Proposta MIP Delta - Copacabana e Ipanema", body))
    t.check("e-mail inédito: FORN-004, MIP, 8%", (t.val("fornecedor"), t.val("categoria"), t.val("desconto")) == ("FORN-004", "MIP", 8.0),
            (t.val("fornecedor"), t.val("categoria"), t.val("desconto")))
    t.check("lojas Copacabana e Ipanema", sorted(t.val("lojas") or []) == ["LOJA-101", "LOJA-102"], t.val("lojas"))
    t.check("vigência 01/11/2026–30/04/2027", (t.val("inicio"), t.val("fim")) == ("2026-11-01", "2027-04-30"), (t.val("inicio"), t.val("fim")))
    t.close_interp(absent=t.missing_empty())
    t.ack_all()
    t.close_rules()
    t.check("pronta para aprovação (Coordenação)", t.p["state"] == "PRONTA_PARA_APROVACAO" and t.p["required"]["level"] == 1, (t.p["state"], t.p["required"]))
    t.approve("coord")
    t.register()
    t.check("e-mail inédito cadastrado no ERP simulado", t.p["state"] == "CADASTRADA", t.p["state"])


def sc_verba_comite(t: Flow):
    body = ("Renata, bom dia.\n\nPara o lançamento da linha de suplementos Epsilon, oferecemos verba de exposição de "
            "R$ 25.000,00 (total) para ilha promocional nas lojas do Leblon e da Gávea, de 01/11/2026 a 31/01/2027.\n\n"
            "Contrapartida: ilha promocional na entrada das duas lojas durante toda a vigência.\n\nAtt,\nPaulo Reis\nEpsilon Farma")
    t.analyze(t.receive_text("Paulo Reis <paulo.reis@epsilonfarma.com.br>", "Verba de exposição - Suplementos Epsilon", body))
    t.check("verba lida = R$ 25.000 e sem desconto", t.val("verba") == 25000.0 and t.f("desconto") is None, (t.val("verba"), t.f("desconto")))
    t.check("lojas Leblon e Gávea", sorted(t.val("lojas") or []) == ["LOJA-103", "LOJA-132"], t.val("lojas"))
    t.close_interp(absent=t.missing_empty())
    t.check("acima de R$ 20.000 exige Comitê (RB03)", "RB03:COMITE" in t.ids(), sorted(t.ids()))


def sc_nao_proposta(t: Flow):
    body = "Oi Renata, obrigado pela reunião de hoje. Semana que vem envio a proposta formal com os números.\n\nAbs,\nCarlos"
    t.analyze(t.receive_text("Carlos Melo <carlos@fornecedorx.com.br>", "Obrigado pela reunião", body))
    t.check("sinalizado como não-proposta", (t.p.get("flags") or {}).get("not_a_proposal") is True, (t.p.get("flags"), t.p.get("flag_sources")))
    t.check("o próprio agente declarou que não é proposta", "agente" in ((t.p.get("flag_sources") or {}).get("not_a_proposal") or ""), t.p.get("flag_sources"))
    t.check("nenhum componente inventado", t.f("desconto") is None and t.f("verba") is None, [f["key"] for f in t.p["fields"]])
    t.close_interp(absent=t.missing_empty())
    t.check("RB08 sem componente", "RB08:SEM_COMPONENTE" in t.ids(), sorted(t.ids()))


SCENARIOS = {
    "Dataset · Beta": sc_beta, "Dataset · Gama": sc_gama, "Dataset · Alfa": sc_alfa, "Dataset · Nutrivida": sc_nutrivida,
    "Variação · Beta com 9%": sc_beta_9, "Variação · Beta com 22%": sc_beta_22, "Variação · Beta com 41%": sc_beta_41,
    "Variação · Beta fim em 31/02/2027": sc_beta_data_invalida, "Variação · Beta início retroativo": sc_beta_retro,
    "Variação · Beta vigência de 15 meses": sc_beta_long, "Variação · Alfa sem campanha (22%)": sc_alfa_sem_campanha,
    "Variação · Nutrivida sem a nota": sc_nutrivida_sem_nota, "Inédito · Delta MIP 8%": sc_inedito_delta,
    "Inédito · Epsilon verba R$ 25 mil": sc_verba_comite, "Inédito · e-mail sem proposta": sc_nao_proposta,
}


def run_one(name, fn):
    t = None
    try:
        t = Flow(name)
        fn(t)
        err = None
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    return {"cenario": name, "erro": err, "checks": t.checks if t else [], "segundos_ia": getattr(t, "analysis_secs", None),
            "snapshot": t.snapshot() if t and t.v.get("proposal") else None}


def wait_for_commit(sha: str | None, limit_s=900):
    if not sha:
        return None
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit_s:
        try:
            h = httpx.get(BASE + "/api/health", timeout=30).json()
            if h.get("commit") and sha.startswith(h["commit"]):
                return h
        except Exception:  # noqa: BLE001
            pass
        time.sleep(15)
    return httpx.get(BASE + "/api/health", timeout=30).json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="e2e-report")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    if not BASE:
        sys.exit("Defina BASE_URL.")
    health = wait_for_commit(os.environ.get("EXPECT_SHA"))
    deep = httpx.get(BASE + "/api/health/deep", timeout=60).json()
    sel = {k: v for k, v in SCENARIOS.items() if not a.only or a.only.lower() in k.lower()}
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda kv: run_one(*kv), sel.items()))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "resultado.json").write_text(json.dumps({"base": BASE, "health": health, "health_deep": deep, "resultados": results},
                                                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lines = [f"# Verificação ponta a ponta — {BASE}", "", f"Commit publicado: `{(health or deep).get('commit')}` · modelo: `{deep['llm'].get('model')}` · "
             f"banco: {deep['storage'].get('detail')}", ""]
    total = ok = 0
    for r in results:
        n_ok = sum(c["ok"] for c in r["checks"])
        total += len(r["checks"]) + (1 if r["erro"] else 0)
        ok += n_ok
        status = "OK" if not r["erro"] and n_ok == len(r["checks"]) else "FALHOU"
        lines += [f"## {r['cenario']} — {status} ({n_ok}/{len(r['checks'])})" + (f" · leitura {r['segundos_ia']} s" if r["segundos_ia"] else "")]
        for c in r["checks"]:
            lines.append(f"- {'✅' if c['ok'] else '❌'} {c['check']}" + ("" if c["ok"] else f" — obtido: `{json.dumps(c['got'], ensure_ascii=False, default=str)[:400]}`"))
        if r["erro"]:
            lines.append(f"- ❌ erro: `{r['erro'][:600]}`")
        s = r["snapshot"]
        if s:
            lines.append(f"- Resumo do agente: _{s['resumo_agente']}_")
            lines.append(f"- Sinais: `{json.dumps(s['sinais'], ensure_ascii=False)}` · origem `{json.dumps(s['origem_sinais'], ensure_ascii=False)}`")
            lines.append(f"- Estado final: {s['estado']} · decisão `{json.dumps(s['decisao'], ensure_ascii=False)}` · apontamentos: {', '.join(s['apontamentos'])}")
        lines.append("")
    lines.insert(3, f"**{ok}/{total} verificações aprovadas**")
    (out / "RELATORIO.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
