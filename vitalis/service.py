"""Serviço do Vitalis (central de condições comerciais) — contrato de API do protótipo (handoff §3).

Fluxo por mensagem: caixa de entrada → interpretação (IA) → regras → conclusão.
Invariantes no servidor: IA sem poder de escrita; aprovação humana vinculada ao hash da revisão;
revalidação antes do cadastro; sucesso somente com 201 persistido; repetição com a mesma chave.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .config import Settings
from .dataset import get_example, load_examples
from .engine import (
    APPROVERS,
    AUTO_RULES,
    CATEGORIAS,
    POLICY_VERSION,
    REJECT_REASONS,
    ROUTES,
    add_days,
    now_iso,
    open_findings,
    recompute,
)
from .erp_mock import ERPTimeout, MockERP, initial_erp_state
from .extraction import Extractor, run_extraction
from .interpretation import to_fields
from .store import SessionNotFound, SessionStore, VersionConflict

SCHEMA = 3  # 3: caixa de entrada nasce com os 4 e-mails do dataset
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def rand(n: int) -> str:
    return "".join(random.choice(_ALPHABET) for _ in range(n))


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False, findings: list | None = None, **extra: Any):
        super().__init__(message)
        self.status, self.code, self.message, self.retryable = status, code, message, retryable
        self.findings, self.extra = findings or [], extra


def seed_inbox() -> list[dict]:
    """A sessão nasce com os 4 e-mails originais do dataset na caixa de entrada (recebidos, ainda não analisados).
    `received_at` é a data do próprio e-mail; mais recentes primeiro, como numa caixa real."""
    items = []
    for fx in load_examples().values():
        items.append({"id": "MSG-" + rand(5), "received_at": fx.sent_at or now_iso(), "source": Service._fixture_source(fx),
                      "proposal": None, "approval": None, "operation": None})
    return sorted(items, key=lambda i: i["received_at"], reverse=True)


def new_state(settings: Settings, llm_calls: int = 0) -> dict:
    return {"schema": SCHEMA, "created_at": now_iso(), "business_date": settings.business_date.isoformat(),
            "erp": initial_erp_state(), "inbox": seed_inbox(), "current": None,
            "test": {"erp_fail_next": False, "erp_random_503": False, "auto_conclusion": True},
            "events": [], "counters": {"llm_calls": llm_calls}}


def _event(S: dict, type_: str, detail: str) -> None:
    S["events"].append({"at": now_iso(), "type": type_, "detail": detail})
    del S["events"][:-200]


def _current(S: dict) -> dict | None:
    return next((i for i in S["inbox"] if i["id"] == S.get("current")), None)


def _canon(t: str | None) -> str:
    return (t or "").replace("\r\n", "\n").strip()


def _store_label(nome: str) -> str:
    return re.sub(r"^DROG\s+(VITALIS\s+)?", "", nome)


class Service:
    def __init__(self, store: SessionStore, settings: Settings, extractor_factory: Callable[[], Extractor]):
        self.store, self.settings, self.extractor_factory = store, settings, extractor_factory

    # ------------------------------------------------------------ sessão
    def ensure(self, sid: str | None) -> tuple[str, dict, int]:
        if sid:
            try:
                S, v = self.store.load(sid)
                if S.get("schema") == SCHEMA:
                    return sid, S, v
                S2 = new_state(self.settings)  # sessão de versão anterior do backend: recomeça limpa
                return sid, S2, self.store.save(sid, S2, v)
            except SessionNotFound:
                pass
        S = new_state(self.settings)
        sid, v = self.store.create(S)
        return sid, S, v

    def _check(self, b: dict, v: int) -> None:
        ev = b.get("expected_version")
        if ev is not None and ev != v:
            raise ApiError(409, "VERSION_CONFLICT", "A sessão foi alterada. Recarregue o estado antes de continuar.", retryable=True)

    def _save(self, sid: str, S: dict, v: int) -> int:
        try:
            return self.store.save(sid, S, v)
        except VersionConflict as exc:
            raise ApiError(409, "VERSION_CONFLICT", "Outra operação alterou a sessão. Recarregue e repita.", retryable=True) from exc

    def _recompute(self, S: dict, item: dict | None = None) -> None:
        item = item or _current(S)
        if item and item.get("proposal"):
            recompute(item, S["erp"], S["business_date"], S["test"], S["events"])

    # ------------------------------------------------------------ visão
    def view(self, sid: str, S: dict, v: int) -> dict:
        item = _current(S)
        if item and item.get("proposal"):
            recompute(item, S["erp"], S["business_date"], S["test"], [])  # derivação pura; nada é gravado no GET
        inbox = []
        for i in S["inbox"]:
            p = i.get("proposal")
            d = p.get("decision") if p else None
            inbox.append({
                "id": i["id"], "received_at": i["received_at"], "kind": i["source"]["kind"], "from": i["source"].get("from"),
                "subject": i["source"].get("subject"), "has_attachment": bool(i["source"].get("attachment")),
                "derived_from": i["source"].get("derived_from"), "proposal_id": p["id"] if p else None,
                "state": p["state"] if p else None, "stage": p["stage"] if p else None,
                "decision": {"type": d["type"], "route": d.get("route"), "automatic": bool(d.get("automatic")),
                             "reason_code": d.get("reason_code"), "reason_label": REJECT_REASONS.get(d.get("reason_code") or ""),
                             "note": d.get("note") or "", "actor": d.get("actor"), "at": d.get("at")} if d else None,
                "auto": ((p.get("conclusion") or {}).get("auto") or {}).get("rule") if p else None,
            })
        erp = S["erp"]
        return {
            "session_id": hashlib.sha256(sid.encode()).hexdigest()[:16],  # identificador exibível; o id real fica no cookie
            "version": v, "business_date": S["business_date"], "policy_version": POLICY_VERSION,
            "erp_snapshot_at": erp.get("snapshot_captured_at"), "current_item": S.get("current"), "inbox": inbox,
            "proposal": item.get("proposal") if item else None, "approval": item.get("approval") if item else None,
            "operation": item.get("operation") if item else None, "test_mode": S["test"], "events": S["events"][-30:],
            "catalog": {
                "fornecedores": [{"cod": f["cod_fornecedor"], "nome": f["razao_social"], "status": f["status"]} for f in erp["fornecedores"]["itens"]],
                "lojas": [{"cod": l["cod_loja"], "nome": _store_label(l["nome"])} for l in erp["lojas"]["itens"]],
                "categorias": CATEGORIAS, "aprovadores": APPROVERS, "rotas": ROUTES, "motivos_rejeicao": REJECT_REASONS,
            },
            "auto_rules": AUTO_RULES,
            "erp_condicoes": erp["condicoes"]["itens"],
            "backend": {"llm_configured": bool(self.settings.openai_api_key), "model": self.settings.openai_model,
                        "storage": self.settings.storage_backend, "llm_calls": S["counters"]["llm_calls"],
                        "llm_limit": self.settings.max_analyses_per_session},
        }

    def state(self, sid: str | None) -> tuple[str, dict]:
        sid, S, v = self.ensure(sid)
        return sid, self.view(sid, S, v)

    # ------------------------------------------------------------ entrada
    def _source(self, b: dict) -> dict:
        mode = b.get("mode")
        if mode == "dataset_example":
            fx = get_example(b.get("example_id") or "")
            if not fx:
                raise ApiError(422, "EXAMPLE_NOT_FOUND", "Exemplo inexistente.")
            return self._fixture_source(fx)
        if mode != "manual_simulation":
            raise ApiError(422, "INVALID_MODE", "Modalidade de entrada inválida.")
        body = b.get("body") or ""
        if not body.strip():
            raise ApiError(422, "EMPTY_BODY", "O corpo do e-mail é obrigatório.")
        if len(body) > self.settings.max_body_chars:
            raise ApiError(422, "BODY_TOO_LARGE", f"O corpo excede {self.settings.max_body_chars:,} caracteres.".replace(",", "."))
        csv = b.get("csv") or None
        if csv and len(csv.encode("utf-8")) > self.settings.max_attachment_bytes:
            raise ApiError(422, "ATTACHMENT_TOO_LARGE", "O anexo excede 200 KB.")
        name = (b.get("csv_name") or "anexo.csv")[:120]
        if csv and not name.lower().endswith(".csv"):
            raise ApiError(422, "INVALID_FILE", "Apenas arquivos .csv são aceitos.")
        derived = b.get("derived_from_example") or None
        if derived:
            fx = get_example(derived)
            if fx is None:
                raise ApiError(422, "EXAMPLE_NOT_FOUND", "Exemplo de origem inexistente.")
            same = (_canon(b.get("sender")) == _canon(fx.sender) and _canon(b.get("subject")) == _canon(fx.subject)
                    and _canon(body) == _canon(fx.body) and _canon(csv) == _canon(fx.attachment.content if fx.attachment else None))
            if same:  # conteúdo idêntico ao original: é a fixture e preserva o Message-ID real
                return self._fixture_source(fx)
        return {"kind": "EDITED_EXAMPLE" if derived else "MANUAL_SIMULATION", "ref": "SIM-" + str(uuid.uuid4()), "message_id": None,
                "from": (b.get("sender") or "").strip()[:320], "to": None, "date": None, "received_simulated_at": now_iso(),
                "subject": (b.get("subject") or "").strip()[:300], "body": body,
                "attachment": {"name": name, "content": csv} if csv else None, "derived_from": derived}

    @staticmethod
    def _fixture_source(fx) -> dict:
        from .dataset import EXAMPLE_FILES

        return {"kind": "DATASET_EMAIL", "ref": fx.message_id, "message_id": fx.message_id, "in_reply_to": fx.in_reply_to,
                "file": EXAMPLE_FILES[fx.example_id], "from": fx.sender, "to": fx.to, "cc": fx.cc, "date": fx.date_header,
                "subject": fx.subject, "body": fx.body,
                "attachment": {"name": fx.attachment.filename, "content": fx.attachment.content} if fx.attachment else None,
                "derived_from": None}

    def receive(self, sid: str | None, b: dict) -> tuple[str, dict]:
        sid, S, v = self.ensure(sid)
        src = self._source(b)
        if len(S["inbox"]) >= 30:
            raise ApiError(422, "INBOX_FULL", "A caixa de entrada desta sessão atingiu 30 mensagens. Reinicie a demonstração.")
        it = {"id": "MSG-" + rand(5), "received_at": now_iso(), "source": src, "proposal": None, "approval": None, "operation": None}
        S["inbox"].insert(0, it)
        _event(S, "RECEBIMENTO", f"{it['id']} · {src['kind']}")
        return sid, self.view(sid, S, self._save(sid, S, v))

    def close_item(self, sid: str | None) -> tuple[str, dict]:
        sid, S, v = self.ensure(sid)
        S["current"] = None
        return sid, self.view(sid, S, self._save(sid, S, v))

    # ------------------------------------------------------------ interpretação (IA)
    def analyze(self, sid: str | None, b: dict) -> tuple[str, dict]:
        sid, S, v = self.ensure(sid)
        if not b.get("item_id") and b.get("mode"):  # compatibilidade: análise direta sem passar pela caixa
            sid, view = self.receive(sid, b)
            S, v = self.store.load(sid)
            b = {"item_id": S["inbox"][0]["id"]}
        item = next((i for i in S["inbox"] if i["id"] == b.get("item_id")), None)
        if not item:
            raise ApiError(404, "ITEM_NOT_FOUND", "Mensagem não encontrada na caixa de entrada.")
        if item.get("proposal"):
            S["current"] = item["id"]
            return sid, self.view(sid, S, self._save(sid, S, v))
        if S["counters"]["llm_calls"] >= self.settings.max_analyses_per_session:
            raise ApiError(429, "ANALYSIS_LIMIT", "Limite de leituras por IA desta demonstração atingido.")
        try:
            extractor = self.extractor_factory()
        except ApiError:
            raise
        llm, meta = run_extraction(extractor, item["source"], copy.deepcopy(S["erp"]), self.settings.business_date)
        S["counters"]["llm_calls"] += 1
        if llm is None:
            _event(S, "EXTRACAO_FALHOU", meta.get("error") or "")
            self._save(sid, S, v)
            reason = meta.get("error_reason") or "motivo não identificado"
            raise ApiError(502, "EXTRACTION_FAILED", f"A leitura por IA falhou: {reason}. Nada foi preenchido automaticamente.",
                           retryable=True, detail=meta.get("error"))
        mapped = to_fields(llm, item["source"], S["erp"])
        secs = (meta.get("latency_ms") or 0) / 1000
        item["proposal"] = {
            "id": "PRP-" + rand(6), "item_id": item["id"], "created_at": now_iso(), "source": item["source"], "revision": 1,
            "stage": "INTERPRETACAO", "auto_allowed": False, "fields": mapped["fields"], "evidence": mapped["evidence"],
            "csv_highlights": mapped["csv_highlights"], "flags": mapped["flags"], "acks": {}, "decision": None,
            "ai_summary": mapped["summary_ai"], "ambiguities": mapped["ambiguities"],
            "extractor": f"Agente de IA · {meta.get('model')} · {secs:.1f} s".replace(".", ","), "extraction_meta": meta,
        }
        item["approval"] = item["operation"] = None
        S["current"] = item["id"]
        self._recompute(S, item)
        _event(S, "ANALISE", f"{item['proposal']['id']} · {item['id']}")
        return sid, self.view(sid, S, self._save(sid, S, v))

    # ------------------------------------------------------------ revisão / decisão
    def _apply_review(self, S: dict, b: dict) -> dict:
        item = _current(S)
        p = item.get("proposal") if item else None
        if not p or p["id"] != b.get("proposal_id"):
            raise ApiError(404, "PROPOSAL_NOT_FOUND", "Proposta não encontrada na sessão.")
        if p["state"] in ("CADASTRADA", "REJEITADA"):
            raise ApiError(409, "STATE_FINAL", f"Proposta em estado final ({p['state']}).")
        op, approval = item.get("operation"), item.get("approval")
        if op and op["confirmation_status"] != "CONFIRMED" and (b.get("changes") or b.get("decision")):
            raise ApiError(409, "OPERATION_UNCONFIRMED", "Há operação de registro sem confirmação. Repita o envio com a mesma chave antes de alterar a proposta.")
        notice = None
        if b.get("decision") == "REJEITAR":
            if b.get("reason_code") not in REJECT_REASONS:
                raise ApiError(422, "REASON_REQUIRED", "Selecione um motivo de rejeição padronizado.")
            p["decision"] = {"type": "REJEITAR", "reason_code": b["reason_code"], "note": (b.get("note") or "").strip()[:1000],
                             "actor": "Revisor (simulado)", "automatic": False, "at": now_iso(), "state_at_decision": p["state"],
                             "open_findings": open_findings(p)}
            if approval:
                approval["status"] = "INVALIDADA"
            self._recompute(S, item)
            _event(S, "REJEICAO", b["reason_code"])
            return {"item": item}
        if b.get("decision") == "ESCALAR":
            opt = next((o for o in (p.get("conclusion") or {}).get("options", []) if o["code"] == "ESCALAR:" + str(b.get("route"))), None)
            if not opt:
                raise ApiError(422, "ROUTE_REQUIRED", "Destino de escalonamento inválido.")
            if not opt["available"]:
                raise ApiError(409, "OPTION_UNAVAILABLE", opt["unavailable_reason"] or "Opção indisponível.")
            p["decision"] = {"type": "ESCALAR", "route": b["route"], "note": (b.get("note") or "").strip()[:1000], "actor": "Revisor (simulado)",
                             "automatic": False, "at": now_iso(), "state_at_decision": p["state"], "open_findings": open_findings(p)}
            self._recompute(S, item)
            _event(S, "ESCALONAMENTO", b["route"])
            return {"item": item}

        before = p.get("revision_hash")
        lojas = {l["cod_loja"] for l in S["erp"]["lojas"]["itens"]}
        for k, val in (b.get("changes") or {}).items():
            f = next((x for x in p["fields"] if x["key"] == k), None)
            if not f:
                raise ApiError(422, "UNKNOWN_FIELD", "Campo inexistente: " + k)
            if f["editor"] == "supplier" and val and not any(x["cod_fornecedor"] == val for x in S["erp"]["fornecedores"]["itens"]):
                raise ApiError(422, "INVALID_SUPPLIER", "Fornecedor fora do catálogo.")
            if f["editor"] == "category" and val and val not in CATEGORIAS:
                raise ApiError(422, "INVALID_CATEGORY", "Categoria fora do catálogo.")
            if f["editor"] == "stores" and val and (not isinstance(val, list) or ("REDE" in val and len(val) > 1) or any(c != "REDE" and c not in lojas for c in val)):
                raise ApiError(422, "INVALID_STORES", "Lojas inválidas: use códigos do cadastro ou somente REDE (RB09).")
            if f["editor"] in ("percent", "money") and val is not None:
                try:
                    val = round(float(val), 2)
                except (TypeError, ValueError) as exc:
                    raise ApiError(422, "INVALID_VALUE", f["label"] + ": valor numérico inválido.") from exc
                if val <= 0:
                    raise ApiError(422, "INVALID_VALUE", f["label"] + ": informe um número maior que zero.")
            if f["editor"] == "date" and val:
                try:
                    datetime.strptime(val, "%Y-%m-%d")
                except ValueError as exc:
                    raise ApiError(422, "INVALID_DATE", f["label"] + ": data inválida.") from exc
            if f["editor"] == "text" and isinstance(val, str):
                val = val.strip()[:500]
            if json.dumps(f["value"]) == json.dumps(val):
                continue
            f["corrected_from"] = f.get("display")
            f["value"], f["origin_kind"], f["confirmation_status"], f["reason"] = val, "HUMAN_CORRECTED", "CONFIRMED", None
        for k in b.get("confirmations") or []:
            f = next((x for x in p["fields"] if x["key"] == k), None)
            if f and f["value"] is not None and f["confirmation_status"] == "PENDING":
                f["confirmation_status"] = "CONFIRMED"
        for k in b.get("absent") or []:
            f = next((x for x in p["fields"] if x["key"] == k), None)
            if f and (f["value"] is None or (isinstance(f["value"], list) and not f["value"])):
                f["confirmation_status"] = "ABSENT"
        from .engine import fields_hash

        p["revision_hash"] = fields_hash(p["fields"])
        changed = p["revision_hash"] != before
        if changed:
            p["revision"] += 1
            p["acks"], p["decision"], p["stage"], p["auto_allowed"] = {}, None, "INTERPRETACAO", False
            if approval and approval.get("status") == "VALIDA":
                approval["status"] = "INVALIDADA"
                _event(S, "APROVACAO_INVALIDADA", f"RB11 · revisão {p['revision']}")
            _event(S, "REVISAO", f"rev {p['revision']} · " + ", ".join((b.get("changes") or {}).keys()))
            if b.get("acknowledged_warning_ids"):
                notice = "A revisão mudou: ciências anteriores expiraram e precisam ser registradas sobre a nova revisão."
        elif isinstance(b.get("acknowledged_warning_ids"), list):
            p["acks"] = {wid: p["revision_hash"] for wid in b["acknowledged_warning_ids"]}
        self._recompute(S, item)
        return {"item": item, "notice": notice}

    def review(self, sid: str | None, b: dict) -> tuple[str, dict]:
        sid, S, v = self.ensure(sid)
        self._check(b, v)
        r = self._apply_review(S, b)
        view = self.view(sid, S, self._save(sid, S, v))
        if r.get("notice"):
            view["notice"] = r["notice"]
        return sid, view

    def close_interpretation(self, sid: str | None, b: dict) -> tuple[str, dict]:
        sid, S, v = self.ensure(sid)
        self._check(b, v)
        r = self._apply_review(S, b)
        p = r["item"]["proposal"]
        open_ = [f for f in p["fields"] if f["confirmation_status"] == "PENDING"]
        if open_:  # nada é gravado: o rascunho continua na tela do revisor
            raise ApiError(422, "INTERPRETATION_OPEN", f"Ainda há {len(open_)} dado(s) a verificar: " + ", ".join(f["label"] for f in open_) + ".")
        if p["stage"] == "INTERPRETACAO":
            p["stage"] = "REGRAS"
            _event(S, "INTERPRETACAO_CONCLUIDA", f"rev {p['revision']}")
        self._recompute(S, r["item"])
        return sid, self.view(sid, S, self._save(sid, S, v))

    def close_rules(self, sid: str | None, b: dict) -> tuple[str, dict]:
        sid, S, v = self.ensure(sid)
        self._check(b, v)
        item = _current(S)
        p = item.get("proposal") if item else None
        if not p or p["id"] != b.get("proposal_id"):
            raise ApiError(404, "PROPOSAL_NOT_FOUND", "Proposta não encontrada na sessão.")
        if p["stage"] == "INTERPRETACAO":
            raise ApiError(409, "INTERPRETATION_OPEN", "Conclua a interpretação antes de validar as regras.")
        p["stage"] = "CONCLUSAO"
        if b.get("apply_auto") is not False:
            p["auto_allowed"] = True
        _event(S, "REGRAS_CONCLUIDAS", f"rev {p['revision']}" + (" · retorno escolhido pelo revisor" if b.get("apply_auto") is False else ""))
        self._recompute(S, item)
        return sid, self.view(sid, S, self._save(sid, S, v))

    # ------------------------------------------------------------ aprovação e cadastro
    def approve(self, sid: str | None, b: dict) -> tuple[str, dict]:
        sid, S, v = self.ensure(sid)
        self._check(b, v)
        item = _current(S)
        p = item.get("proposal") if item else None
        if not p or p["id"] != b.get("proposal_id"):
            raise ApiError(404, "PROPOSAL_NOT_FOUND", "Proposta não encontrada.")
        self._recompute(S, item)
        if b.get("revision_hash") != p["revision_hash"]:
            raise ApiError(409, "REVISION_CHANGED", "A revisão aprovada não é a revisão vigente.")
        if p["state"] != "PRONTA_PARA_APROVACAO":
            raise ApiError(409, "NOT_READY", f"Aprovação indisponível no estado {p['state']}.",
                           findings=[f for f in p["findings"] if f["severity"] in ("BLOCKER", "REQUIRES_CONFIRMATION") or (f["severity"] == "WARNING" and not f.get("acknowledged"))])
        ap = next((a for a in APPROVERS if a["id"] == b.get("approver_id")), None)
        if not ap:
            raise ApiError(422, "APPROVER_REQUIRED", "Selecione o perfil de aprovador.")
        if p["required"]["level"] is None or ap["nivel"] < p["required"]["level"]:
            raise ApiError(403, "ALCADA_INSUFICIENTE", f"{ap['cargo']} não possui alçada para esta negociação. Exigida: {p['required']['role']} (Política §5).")
        item["approval"] = {"approval_id": "APV-" + rand(6), "proposal_id": p["id"], "revision": p["revision"], "revision_hash": p["revision_hash"],
                            "policy_version": POLICY_VERSION, "approver_role": ap["cargo"], "approver_name": ap["nome"],
                            "approver_registration": ap["matricula"], "decision_timestamp_real": now_iso(),
                            "decision_business_date": S["business_date"], "status": "VALIDA", "simulated": True}
        self._recompute(S, item)
        _event(S, "APROVACAO", f"{ap['cargo']} · rev {p['revision']}")
        return sid, self.view(sid, S, self._save(sid, S, v))

    @staticmethod
    def _payload(p: dict, approval: dict) -> dict:
        V = lambda k: next((f for f in p["fields"] if f["key"] == k), {}).get("value")  # noqa: E731
        c = p["components"][0]
        pl: dict[str, Any] = {"cod_fornecedor": V("fornecedor"), "cod_categoria": V("categoria"), "tipo": c["tipo"], "lojas": V("lojas")}
        if c["tipo"] == "DESCONTO_PERCENTUAL":
            pl["percentual"] = V("desconto")
        else:
            pl["valor"] = V("verba")
        pl["data_inicio"], pl["data_fim"] = V("inicio"), V("fim")
        if V("contrapartida"):
            pl["contrapartida"] = str(V("contrapartida"))[:500]
        if p.get("substitution"):
            pl["cod_condicao_substituida"] = p["substitution"]["cod_condicao"]
        pl["aprovador"] = {"matricula": approval["approver_registration"], "nome": approval["approver_name"]}
        pl["origem"] = {"tipo": "EMAIL", "referencia": p["source"].get("message_id") or p["source"]["ref"]}
        return pl

    def register(self, sid: str | None, b: dict) -> tuple[str, dict]:
        sid, S, v = self.ensure(sid)
        self._check(b, v)
        item = _current(S)
        p = item.get("proposal") if item else None
        if not p or p["id"] != b.get("proposal_id"):
            raise ApiError(404, "PROPOSAL_NOT_FOUND", "Proposta não encontrada.")
        self._recompute(S, item)
        a = item.get("approval")
        if not a or a["status"] != "VALIDA" or a["approval_id"] != b.get("approval_id") or a["revision_hash"] != p["revision_hash"]:
            raise ApiError(409, "APPROVAL_INVALID", "Não há aprovação válida para a revisão vigente.")
        if not p["registerable"]:
            raise ApiError(409, "MULTI_COMPONENT", "Proposta com múltiplos componentes não é registrável neste fluxo.")
        op = item.get("operation")
        if op and op["confirmation_status"] == "CONFIRMED":
            # repetição após confirmação: replay idempotente com a mesma chave (prova de não duplicação)
            pass
        elif op and datetime.now(timezone.utc) - datetime.fromisoformat(op["created_at_real"]) > timedelta(hours=24):
            raise ApiError(409, "IDEMPOTENCY_WINDOW_EXPIRED", "Janela de 24 h expirada sem confirmação. Reenvio bloqueado: requer verificação manual fora da demonstração.")
        if not op:
            if p["counts"]["blockers"]:
                raise ApiError(409, "BLOCKED", "Revalidação encontrou bloqueios.", findings=[f for f in p["findings"] if f["severity"] == "BLOCKER"])
            op = item["operation"] = {"operation_id": "OP-" + rand(6), "approval_id": a["approval_id"], "revision_hash": a["revision_hash"],
                                      "erp_payload": self._payload(p, a), "idempotency_key": str(uuid.uuid4()), "attempts": [],
                                      "last_error": None, "erp_response": None, "confirmation_status": "PENDING", "created_at_real": now_iso()}
            _event(S, "OPERACAO", f"{op['operation_id']} · {op['idempotency_key']}")
            v = self._save(sid, S, v)  # write-ahead: chave e payload persistidos antes do envio

        erp = MockERP(S["erp"], self.settings.business_date)
        test = S["test"]
        tries, r = 0, None
        while True:
            simulate = "503" if test.get("erp_fail_next") or (test.get("erp_random_503") and random.random() < 0.2) else None
            tries += 1
            try:
                r = erp.post_condicao(copy.deepcopy(op["erp_payload"]), op["idempotency_key"], simulate)
            except ERPTimeout:
                r = {"status_http": 0, "headers": {}, "body": {"error": "TIMEOUT", "message": "Sem resposta do ERP."}}
            http, body = r["status_http"], r["body"]
            replay = r["headers"].get("Idempotent-Replay") == "true"
            retry_after = int(r["headers"].get("Retry-After", 0)) or None
            result = ("Replay idempotente — resposta original, sem novo cadastro" if replay else "Condição cadastrada") if http == 201 \
                else body.get("error", "ERRO") + (" · reenvio automático" if http == 503 and tries < 3 else "")
            op["attempts"].append({"n": len(op["attempts"]) + 1, "at": now_iso(), "http": http, "result": result, "retry_after": retry_after})
            if http == 503 and tries < 3:
                continue
            break
        test["erp_fail_next"] = False
        if http == 201:
            op["confirmation_status"], op["last_error"] = "CONFIRMED", None
            if not op["erp_response"]:
                op["erp_response"] = {"http": 201, "cod_condicao": body["cod_condicao"], "confirmed_at": now_iso()}
            _event(S, "REPLAY" if replay else "CADASTRO", body["cod_condicao"])
        else:
            if op["confirmation_status"] != "CONFIRMED":
                op["confirmation_status"] = "FAILED"
            op["last_error"] = {"http": http, "code": body.get("error"), "message": body.get("message"), "retry_after": retry_after,
                                "retryable": http in (0, 429, 503), "correlation_id": "cor-" + uuid.uuid4().hex[:8],
                                "fields": body.get("campos")}
            _event(S, "FALHA_REGISTRO", f"{http} {body.get('error')}")
        self._recompute(S, item)
        return sid, self.view(sid, S, self._save(sid, S, v))

    # ------------------------------------------------------------ demonstração
    def test_mode(self, sid: str | None, b: dict) -> tuple[str, dict]:
        sid, S, v = self.ensure(sid)
        for k in ("erp_fail_next", "erp_random_503", "auto_conclusion"):
            if k in b:
                S["test"][k] = bool(b[k])
        self._recompute(S)
        return sid, self.view(sid, S, self._save(sid, S, v))

    def reset(self, sid: str | None) -> tuple[str, dict]:
        sid, S, v = self.ensure(sid)
        fresh = new_state(self.settings, llm_calls=S["counters"]["llm_calls"])  # contador de IA sobrevive (D18)
        _event(fresh, "RESET", "Sessão reiniciada a partir do snapshot de 30/09/2026")
        return sid, self.view(sid, fresh, self._save(sid, fresh, v))
