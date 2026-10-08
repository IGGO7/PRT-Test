"""Matriz de validação (§12) exercitada pela API do protótipo, do jeito que a interface chama."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class UI:
    """Reproduz as chamadas da interface (Component.api) contra o backend real."""

    def __init__(self, client):
        self.c = client
        self.v = client.get("/api/state").json()

    def post(self, path, body=None, expect=200):
        r = self.c.post(path, json=body or {})
        assert r.status_code == expect, r.text
        if r.status_code == 200:
            self.v = r.json()
        return r.json()

    @property
    def p(self):
        return self.v["proposal"]

    def f(self, key):
        return next(x for x in self.p["fields"] if x["key"] == key)

    def finding(self, fid):
        return next((x for x in self.p["findings"] if x["id"] == fid), None)

    def receive_example(self, ex):
        self.post("/api/inbox", {"mode": "dataset_example", "example_id": ex})
        return self.v["inbox"][0]["id"]

    def open(self, item_id):
        return self.post("/api/analyze", {"item_id": item_id})

    def close_interp(self, changes=None, confirmations=None, absent=None, expect=200):
        pend = [f["key"] for f in self.p["fields"] if f["confirmation_status"] == "PENDING" and f["value"] not in (None, [])]
        return self.post("/api/interpretation/close", {"proposal_id": self.p["id"], "expected_version": self.v["version"],
                                                       "changes": changes or {}, "confirmations": pend if confirmations is None else confirmations,
                                                       "absent": absent or []}, expect)

    def ack_all(self):
        ids = [f["id"] for f in self.p["findings"] if f["severity"] == "WARNING"]
        return self.post("/api/review", {"proposal_id": self.p["id"], "expected_version": self.v["version"], "acknowledged_warning_ids": ids})

    def close_rules(self, apply_auto=True):
        return self.post("/api/rules/close", {"proposal_id": self.p["id"], "expected_version": self.v["version"], "apply_auto": apply_auto})

    def approve(self, who, expect=200):
        return self.post("/api/approve", {"proposal_id": self.p["id"], "revision_hash": self.p["revision_hash"],
                                          "expected_version": self.v["version"], "approver_id": who}, expect)

    def register(self, expect=200):
        return self.post("/api/erp/register", {"proposal_id": self.p["id"], "approval_id": self.v["approval"]["approval_id"],
                                               "expected_version": self.v["version"]}, expect)


@pytest.fixture
def ui(client):
    return UI(client)


def beta_ready(ui):
    ui.open(ui.receive_example("beta"))
    ui.close_interp()
    ui.ack_all()
    ui.close_rules()


# ------------------------------------------------------------------ fixtures e entrada
def test_examples_are_the_original_dataset_files(client):
    ex = client.get("/api/examples").json()
    assert set(ex) == {"beta", "gama", "alfa", "nutrivida"}
    raw = open("data/dataset/emails/2026-09-29_1830_nutrivida.eml", encoding="utf-8").read()
    assert ex["nutrivida"]["body"] in raw and "Vitalis" in ex["nutrivida"]["body"]      # nada alterado
    assert ex["gama"]["attachment"]["content"] == open("data/dataset/emails/anexos/proposta_gama_q4.csv", encoding="utf-8").read()
    assert ex["nutrivida"]["in_reply_to"] == "<e11a0b9f.20260922141500@vitalis.com.br>"


def test_inbox_starts_with_dataset_emails_unanalyzed(ui):
    inbox = ui.v["inbox"]
    assert ui.v["proposal"] is None and ui.v["business_date"] == "2026-09-30"
    assert len(inbox) == 4 and all(i["kind"] == "DATASET_EMAIL" and i["state"] is None for i in inbox)   # recebidos, não analisados
    assert [i["received_at"] for i in inbox] == sorted((i["received_at"] for i in inbox), reverse=True)
    assert len({i["from"] for i in inbox}) == 4


# ------------------------------------------------------------------ Beta
def test_beta_end_to_end(ui):
    item = ui.receive_example("beta")
    assert ui.v["inbox"][0]["state"] is None                                                 # recebido, não analisado
    ui.open(item)
    p = ui.p
    assert p["stage"] == "INTERPRETACAO" and p["source"]["message_id"].startswith("<c3d8a7b2")
    assert ui.f("desconto")["value"] == 13.0 and ui.f("desconto")["origin_kind"] == "EXPLICIT"
    assert ui.f("categoria")["origin_kind"] == "INFERRED" and ui.f("categoria")["confirmation_status"] == "PENDING"
    assert ui.f("lojas")["value"] == ["LOJA-104", "LOJA-107", "LOJA-112"]
    assert any(e["field"] == "desconto" and e["text"] == "13% de desconto" for e in p["evidence"])
    assert p["extractor"].startswith("Agente de IA")

    # não avança com dado "a verificar"
    ui.close_interp(confirmations=[], expect=422)
    ui.close_interp()
    assert ui.p["stage"] == "REGRAS"
    assert ui.finding("RB10:COND-003")["severity"] == "WARNING"                             # substituição elegível (D07)
    assert ui.finding("D05:COND-003")                                                        # 10% extrato × 11% ERP
    assert ui.p["state"] == "PENDENTE_REVISAO"
    ui.approve("ger", expect=409)                                                            # sem ciência → recusa (T16)
    ui.ack_all()
    assert ui.p["state"] == "PRONTA_PARA_APROVACAO"
    ui.close_rules()
    assert ui.p["conclusion"]["recommended"] == "APROVAR_CADASTRAR"
    assert ui.approve("coord", expect=403)["code"] == "ALCADA_INSUFICIENTE"
    ui.approve("ger")
    assert ui.p["state"] == "APROVADA" and ui.v["approval"]["simulated"]
    ui.register()
    assert ui.p["state"] == "CADASTRADA"
    op = ui.v["operation"]
    assert op["erp_response"]["cod_condicao"] == "COND-011"
    assert op["erp_payload"]["cod_condicao_substituida"] == "COND-003"
    assert op["erp_payload"]["origem"] == {"tipo": "EMAIL", "referencia": "<c3d8a7b2.20260929100500@distribuidorabeta.com.br>"}
    cond3 = next(c for c in ui.v["erp_condicoes"] if c["cod_condicao"] == "COND-003")
    assert cond3["data_fim"] == "2026-09-30" and cond3["encerrada_por"] == "COND-011"
    # persistência após recarregar (T07)
    again = ui.c.get("/api/state").json()
    assert again["proposal"]["state"] == "CADASTRADA"
    # repetição com a mesma chave não duplica (T11)
    key = op["idempotency_key"]
    ui.register()
    assert ui.v["operation"]["idempotency_key"] == key and "Replay" in ui.v["operation"]["attempts"][-1]["result"]
    assert sum(1 for c in ui.v["erp_condicoes"] if c["cod_fornecedor"] == "FORN-002" and c["cod_categoria"] == "HIGIENE_BELEZA") == 3


def test_material_change_invalidates_approval(ui):                                           # RB11 / D12
    beta_ready(ui)
    ui.approve("ger")
    ui.post("/api/review", {"proposal_id": ui.p["id"], "expected_version": ui.v["version"], "changes": {"fim": "2027-02-28"}})
    assert ui.v["approval"]["status"] == "INVALIDADA" and ui.p["stage"] == "INTERPRETACAO" and ui.p["revision"] == 2
    assert ui.f("fim")["origin_kind"] == "HUMAN_CORRECTED"


def test_503_retried_automatically_with_same_key_then_explicit_retry(ui):
    beta_ready(ui)
    ui.approve("dir")
    ui.post("/api/test-mode", {"erp_fail_next": True})
    ui.register()
    op = ui.v["operation"]
    assert ui.p["state"] == "FALHA_REGISTRO" and len(op["attempts"]) == 3                    # nunca sucesso falso (T10)
    assert {a["http"] for a in op["attempts"]} == {503} and op["last_error"]["retryable"]
    assert not any(c["cod_condicao"] == "COND-011" for c in ui.v["erp_condicoes"])
    ui.register()
    assert ui.p["state"] == "CADASTRADA" and ui.v["operation"]["idempotency_key"] == op["idempotency_key"]


def test_version_conflict(ui):
    beta_ready(ui)
    r = ui.c.post("/api/approve", json={"proposal_id": ui.p["id"], "revision_hash": ui.p["revision_hash"],
                                        "expected_version": ui.v["version"] - 1, "approver_id": "ger"})
    assert r.status_code == 409 and r.json()["code"] == "VERSION_CONFLICT"
    assert {"status", "code", "message", "retryable", "findings", "correlation_id"} <= set(r.json())


# ------------------------------------------------------------------ Gama
def test_gama_conflict_attachment_and_normative_block(ui):                                   # T13/T14/T21
    ui.open(ui.receive_example("gama"))
    d = ui.f("desconto")
    assert d["origin_kind"] == "CONFLICT" and d["value"] is None and "12%" in d["raw_value"] and "12,5%" in d["raw_value"]
    assert "proposta_gama_q4.csv" in d["source_location"]
    assert ui.f("verba")["value"] == 8000.0
    assert ui.f("fim")["origin_kind"] == "MISSING" and ui.f("fim")["confirmation_status"] == "PENDING"
    assert "12,5%" in ui.p["csv_highlights"]
    ui.close_interp(changes={"desconto": 12.5}, absent=["fim"])
    assert ui.finding("RB04:FIM")["severity"] == "BLOCKER"
    comp = ui.finding("RB06:COMPONENTES")
    assert comp["resolution_owner"] == "NORMATIVO"
    assert "Diretoria Comercial" in ui.finding("RB03:ALCADA")["message"]                     # R$ 8.000 total → Diretoria
    ui.close_rules()
    assert ui.p["state"] == "BLOQUEADA" and ui.p["decision"] is None                         # dado do revisor pendente: sem automático
    opts = {o["code"]: o for o in ui.p["conclusion"]["options"]}
    assert not opts["APROVAR_CADASTRAR"]["available"] and opts["ESCALAR:DIRETORIA"]["available"]
    ui.post("/api/review", {"proposal_id": ui.p["id"], "expected_version": ui.v["version"], "decision": "ESCALAR", "route": "DIRETORIA", "note": "coexistência"})
    assert ui.p["decision"]["route"] == "DIRETORIA" and not any(c["cod_fornecedor"] == "FORN-003" and c["status"] == "VIGENTE" for c in ui.v["erp_condicoes"])


# ------------------------------------------------------------------ Alfa
def test_alfa_seasonal_routed_to_board(ui):                                                  # T15
    ui.open(ui.receive_example("alfa"))
    assert ui.f("lojas")["value"] == ["REDE"]
    ui.close_interp()
    s = ui.finding("RB13:SAZONAL")
    assert s and s["resolution_owner"] == "NORMATIVO" and "28%" in s["message"]
    assert "invoca campanha sazonal" in s["message"]                                        # fonte caracteriza a campanha
    assert ui.finding("RB05:CONTRA_LOJAS")
    ui.close_rules()
    assert ui.p["state"] == "BLOQUEADA" and ui.p["decision"]["automatic"] and ui.p["decision"]["route"] == "DIRETORIA"
    ui.approve("dir", expect=409)


# ------------------------------------------------------------------ Nutrivida
def test_nutrivida_inactive_and_injection(ui):                                                # T08/T09
    ui.open(ui.receive_example("nutrivida"))
    assert ui.p["flags"]["injection"] and ui.p["flags"]["approval_claim"] == "esta condição já foi pré-aprovada"
    assert ui.p["flag_sources"]["injection"] == "agente+regra" and ui.p["flag_sources"]["approval_claim"] == "agente+regra"
    assert any(e["kind"] == "risk" for e in ui.p["evidence"])
    ui.close_interp()
    assert ui.finding("RB01:FORN-005")["resolution_owner"] == "EXTERNO"
    assert ui.finding("SEG:INSTRUCAO") and ui.finding("RB13:ALEGACAO")
    ui.ack_all()
    ui.close_rules()
    assert ui.p["state"] == "BLOQUEADA"
    assert ui.p["conclusion"]["auto"]["rule"] == "AUTO-01" and ui.p["decision"]["route"] == "CADASTRO"


# ------------------------------------------------------------------ entrada livre
def test_edited_example_uses_modified_text(ui, client):                                      # T20
    fx = client.get("/api/examples").json()["beta"]
    ui.post("/api/inbox", {"mode": "manual_simulation", "sender": fx["from"], "subject": fx["subject"],
                           "body": fx["body"].replace("13% de desconto", "15% de desconto"), "derived_from_example": "beta"})
    ui.open(ui.v["inbox"][0]["id"])
    assert ui.p["source"]["kind"] == "EDITED_EXAMPLE" and ui.p["source"]["message_id"] is None
    assert ui.f("desconto")["value"] == 15.0
    ui.close_interp()
    assert ui.finding("RB07:ORIGEM")


def test_unedited_example_sent_as_text_keeps_identity(ui, client):
    fx = client.get("/api/examples").json()["gama"]
    ui.post("/api/inbox", {"mode": "manual_simulation", "sender": fx["from"], "subject": fx["subject"], "body": fx["body"],
                           "csv": fx["attachment"]["content"], "csv_name": fx["attachment"]["name"], "derived_from_example": "gama"})
    assert ui.v["inbox"][0]["kind"] == "DATASET_EMAIL"


def test_free_text_unknown_supplier(ui):                                                       # T19
    ui.post("/api/inbox", {"mode": "manual_simulation", "sender": "vendas@kappa.example", "subject": "Proposta",
                           "body": "A Kappa Naturais propõe 9% em vitaminas para toda a rede de 01/11/2026 a 31/01/2027."})
    ui.open(ui.v["inbox"][0]["id"])
    f = ui.f("fornecedor")
    assert f["value"] is None and f["confirmation_status"] == "PENDING"
    ui.close_interp(absent=["fornecedor"])
    assert ui.finding("RB16:fornecedor")["severity"] == "BLOCKER"


def test_extraction_failure_is_honest_and_retryable(ui):
    ui.post("/api/inbox", {"mode": "manual_simulation", "body": "texto qualquer"})
    item = ui.v["inbox"][0]["id"]
    r = ui.post("/api/analyze", {"item_id": item}, expect=502)
    assert r["code"] == "EXTRACTION_FAILED" and r["retryable"]
    st = ui.c.get("/api/state").json()
    assert st["proposal"] is None and st["inbox"][0]["state"] is None


def test_sessions_are_isolated_and_reset(client):
    a = UI(client)
    a.receive_example("alfa")
    b = UI(TestClient(client.app))
    assert len(b.v["inbox"]) == 4 and len(a.v["inbox"]) == 5
    a.post("/api/reset")
    assert len(a.v["inbox"]) == 4 and a.v["backend"]["llm_calls"] == 0


@pytest.mark.parametrize("payload,code", [
    ({"mode": "manual_simulation", "body": "  "}, "EMPTY_BODY"),
    ({"mode": "manual_simulation", "body": "x" * 8001}, "BODY_TOO_LARGE"),
    ({"mode": "manual_simulation", "body": "x", "csv": "a;b", "csv_name": "a.exe"}, "INVALID_FILE"),
    ({"mode": "dataset_example", "example_id": "zeta"}, "EXAMPLE_NOT_FOUND"),
])
def test_input_limits(ui, payload, code):
    assert ui.post("/api/inbox", payload, expect=422)["code"] == code


def test_extraction_failure_reports_cause(client):
    from vitalis.extraction import describe_llm_error

    class AuthenticationError(Exception):
        pass

    class NotFoundError(Exception):
        pass

    assert "chave" in describe_llm_error(AuthenticationError("401"), "gpt-5-mini")
    assert "gpt-x" in describe_llm_error(NotFoundError("model_not_found"), "gpt-x")
    assert "créditos" in describe_llm_error(Exception("You exceeded your current quota"), "m")


def test_memory_storage_refused_on_vercel(monkeypatch):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from vitalis.app import create_app
    from vitalis.config import get_settings

    s = replace(get_settings(), on_vercel=True, storage_backend="memory")
    c = TestClient(create_app(settings=s))
    r = c.get("/api/state")
    assert r.status_code == 503 and r.json()["code"] == "STORAGE_NOT_CONFIGURED"
    h = c.get("/api/health").json()
    assert h["storage"]["ok"] is False and h["status"] == "incompleto"


@pytest.mark.parametrize("raw", ["https://abc.supabase.co", "abc.supabase.co", "abc.supabase.co/", "https://abc.supabase.co/rest/v1/", "abc"])
def test_supabase_url_normalized(raw):
    from vitalis.config import normalize_supabase_url

    assert normalize_supabase_url(raw) == "https://abc.supabase.co"


@pytest.mark.parametrize("raw", ["db.gjmiwygmnqkampsxzquf.supabase.co", "https://db.gjmiwygmnqkampsxzquf.supabase.co:5432",
                                 "postgresql://postgres:x@db.gjmiwygmnqkampsxzquf.supabase.co:5432/postgres"])
def test_supabase_db_host_mapped_to_api(raw):
    from vitalis.config import normalize_supabase_url

    assert normalize_supabase_url(raw) == "https://gjmiwygmnqkampsxzquf.supabase.co"
