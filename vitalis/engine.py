"""Motor de regras e derivação de estado — porte 1:1 do protótipo (handoff §4) para Python.

Determinístico, sem IA. Opera sobre a proposta no formato da interface (`fields[]`) e sobre o
estado do ERP simulado da sessão. Referências: POL-COM-004 v3.2 e SSoT v0.3.2.
"""

from __future__ import annotations

import json
import re
import zlib
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .dataset import extrato_by_condition
from .policy import DISCOUNT_CEILING, POLICY_REF, SEASONAL_CATEGORIES, SEASONAL_MAX

POLICY_VERSION = POLICY_REF  # "POL-COM-004 v3.2"
CATEGORIAS = list(DISCOUNT_CEILING.keys())
ROLES = {1: "Coordenação Comercial", 2: "Gerência Comercial", 3: "Diretoria Comercial", 4: "Comitê Comercial"}
APPROVERS = [
    {"id": "coord", "nome": "Paula Mendes", "cargo": "Coordenação Comercial", "nivel": 1, "matricula": "SIM-0101"},
    {"id": "ger", "nome": "Ricardo Lemos", "cargo": "Gerência Comercial", "nivel": 2, "matricula": "SIM-0202"},
    {"id": "dir", "nome": "Helena Duarte", "cargo": "Diretoria Comercial", "nivel": 3, "matricula": "SIM-0303"},
]
LABELS = {"fornecedor": "Fornecedor", "categoria": "Categoria", "lojas": "Lojas", "inicio": "Início da vigência",
          "fim": "Fim da vigência", "contrapartida": "Contrapartida", "desconto": "Desconto percentual", "verba": "Verba de exposição"}
EDITORS = {"fornecedor": "supplier", "categoria": "category", "lojas": "stores", "inicio": "date", "fim": "date",
           "contrapartida": "text", "desconto": "percent", "verba": "money"}
AUTO_RULES = [
    {"id": "AUTO-01", "label": "Bloqueio e encaminhamento ao Cadastro",
     "when": "Fornecedor inativo ou inexistente no ERP (RB01, Política §8), sem confirmações pendentes",
     "outcome": "BLOQUEADA · encaminhada à área de Cadastro", "enabled": True},
    {"id": "AUTO-04", "label": "Encaminhamento à Diretoria Comercial",
     "when": "Bloqueio normativo (precedência indefinida na política, §11), sem confirmações nem dados pendentes do revisor",
     "outcome": "Encaminhada à Diretoria Comercial", "enabled": True},
    {"id": "AUTO-03", "label": "Bloqueio automático por política",
     "when": "Desconto acima do teto sem exceção aplicável ou acima de 25% (RB02/RB03), com campos confirmados",
     "outcome": "BLOQUEADA", "enabled": True},
    {"id": "AUTO-02", "label": "Aprovação ou rejeição automática", "when": "—", "outcome": "—", "enabled": False,
     "disabled_reason": "Não permitida pela fonte: aprovação exige alçada humana (§4.4, Política §10, D03, D10) e rejeição é decisão humana (§25)."},
]
ROUTES = {"DIRETORIA": "Escalar à Diretoria Comercial — interpretação normativa",
          "CADASTRO": "Encaminhar à área de Cadastro — regularização do fornecedor",
          "FORNECEDOR": "Devolver ao fornecedor — complemento ou correção de dados"}
REJECT_REASONS = {"FORA_DA_POLITICA": "Fora da política comercial", "FORNECEDOR_INELEGIVEL": "Fornecedor inelegível",
                  "DADOS_INSUFICIENTES": "Dados insuficientes ou contraditórios", "DUPLICIDADE": "Duplicidade com condição vigente",
                  "DESISTENCIA": "Desistência comercial"}
SEV_ORDER = {"BLOCKER": 0, "REQUIRES_CONFIRMATION": 1, "WARNING": 2, "INFO": 3}
MAX_MONTHS = 12


# ---------------------------------------------------------------- utilidades
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_days(iso: str, n: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=n)).isoformat()


def add_months(iso: str, n: int) -> str:
    d = date.fromisoformat(iso)
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last)).isoformat()


def br(iso: str | None) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}" if iso else "—"


def br_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        d = datetime.fromisoformat(iso).astimezone(timezone(timedelta(hours=-3)))
        return d.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso


def pct_txt(n: float | None) -> str:
    if n is None:
        return "—"
    s = f"{n:.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",") + "%"


def brl(n: float | None) -> str:
    if n is None:
        return "—"
    s = f"{float(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "R$ " + s


def num(s: Any) -> float:
    t = re.sub(r"[^\d,.-]", "", str(s)).replace(".", "").replace(",", ".")
    return float(t)


def fields_hash(fields: list[dict]) -> str:
    """Hash da revisão (valores dos campos) — usado para vincular ciência e aprovação."""
    payload = json.dumps([[f["key"], f.get("value")] for f in fields], ensure_ascii=False, separators=(",", ":"))
    return f"{zlib.crc32(payload.encode()):08x}"


def same_set(a: list, b: list) -> bool:
    return len(a) == len(b) and set(a) == set(b)


def intersects(a: list, b: list) -> bool:
    return "REDE" in a or "REDE" in b or any(x in b for x in a)


def overlap(i1: str, f1: str | None, i2: str, f2: str | None) -> bool:
    return i1 <= (f2 or "9999-12-31") and (f1 or "9999-12-31") >= i2


# ---------------------------------------------------------------- regras
def validate(p: dict, erp: dict, business_date: str) -> dict:
    fields = p["fields"]
    F = lambda k: next((f for f in fields if f["key"] == k), None)  # noqa: E731
    V = lambda k: (F(k) or {}).get("value")  # noqa: E731
    out: list[dict] = []

    def add(id_, rule, sev, msg, flds=None, owner="REVISOR"):
        out.append({"id": id_, "rule_id": rule, "severity": sev, "message": msg, "affected_fields": flds or [],
                    "resolution_owner": owner, "policy_version": POLICY_VERSION})

    forn, cat, lojas, ini, fim = V("fornecedor"), V("categoria"), V("lojas") or [], V("inicio"), V("fim")
    pct, verba, contra = V("desconto"), V("verba"), V("contrapartida")
    comps: list[dict] = []
    if F("desconto"):
        comps.append({"tipo": "DESCONTO_PERCENTUAL", "key": "desconto", "label": "Desconto percentual", "value": pct, "display": pct_txt(pct)})
    if F("verba"):
        comps.append({"tipo": "VERBA_EXPOSICAO", "key": "verba", "label": "Verba de exposição", "value": verba, "display": brl(verba)})
    if not comps:
        add("RB08:SEM_COMPONENTE", "RB08", "BLOCKER", "Nenhum desconto percentual ou verba de exposição identificado nas fontes.")

    for f in fields:
        if f["confirmation_status"] == "ABSENT":
            if f["key"] in ("fornecedor", "categoria", "lojas", "desconto", "verba"):
                add("RB16:" + f["key"], "RB16", "BLOCKER", f"{f['label']}: não consta da fonte (ausência confirmada na interpretação). Sem esse dado a condição não pode ser cadastrada.", [f["key"]])
            continue
        if f["confirmation_status"] != "PENDING":
            continue
        empty = f["value"] is None or (isinstance(f["value"], list) and not f["value"])
        if f["origin_kind"] == "CONFLICT" and empty:
            add("RB16:" + f["key"], "RB16", "REQUIRES_CONFIRMATION", f"{f['label']}: fontes divergentes ({f['raw_value']}). Defina o valor com base em evidência — a interpretação não escolhe entre fontes.", [f["key"]])
        elif empty:
            add("RB16:" + f["key"], "RB16", "REQUIRES_CONFIRMATION", f"{f['label']}: {f.get('reason') or 'sem correspondência segura'}. Informe o valor.", [f["key"]])
        else:
            add("RB16:" + f["key"], "RB16", "REQUIRES_CONFIRMATION", f"{f['label']}: {f.get('reason') or 'valor proposto pela interpretação'}. Confirme ou corrija.", [f["key"]])

    suppliers = erp["fornecedores"]["itens"]
    if forn:
        s = next((x for x in suppliers if x["cod_fornecedor"] == forn), None)
        if not s:
            add("RB01:" + forn, "RB01", "BLOCKER", f"Fornecedor {forn} não consta do cadastro do ERP.", ["fornecedor"])
        elif s["status"] != "ATIVO":
            add("RB01:" + forn, "RB01", "BLOCKER",
                f"{s['cod_fornecedor']} · {s['razao_social']} está {s['status']} no ERP desde {br(s.get('data_inativacao'))} ({s.get('motivo_inativacao')}). "
                "Política §8: somente fornecedores ativos podem ter condições cadastradas; a regularização ocorre junto à área de Cadastro.",
                ["fornecedor"], "EXTERNO")

    if not ini:
        add("RB04:INICIO", "RB04", "BLOCKER", "Data de início não definida. Política §6: início e fim obrigatórios.", ["inicio"])
    if not fim:
        add("RB04:FIM", "RB04", "BLOCKER", "Data de fim não informada em nenhuma fonte. Política §6: toda condição deve ter início e fim definidos no cadastro.", ["fim"])
    if ini and ini < business_date:
        add("RB04:RETRO", "RB04", "BLOCKER", f"Início ({br(ini)}) anterior à data de negócio {br(business_date)}. Política §6 veda início retroativo.", ["inicio"])
    if ini and fim and fim < ini:
        add("RB04:ORDEM", "RB04", "BLOCKER", "Fim anterior ao início.", ["inicio", "fim"])
    if ini and fim and fim >= ini and fim > add_days(add_months(ini, MAX_MONTHS), -1):
        add("RB04:DURACAO", "RB04", "BLOCKER", "Vigência superior a 12 meses (Política §6).", ["inicio", "fim"])

    normative = False
    if pct is not None and cat in DISCOUNT_CEILING:
        teto = DISCOUNT_CEILING[cat]
        if pct > teto:
            if cat in SEASONAL_CATEGORIES and pct <= SEASONAL_MAX:
                normative = True
                seasonal = (p.get("flags") or {}).get("seasonal")
                add("RB13:SAZONAL", "RB13", "BLOCKER",
                    f"{pct_txt(pct)} excede o teto ordinário de {cat} ({pct_txt(teto)}) e a tabela de alçadas veda desconto acima de 25%. "
                    "A nota (*) da política admite até 30% para Genéricos e MIP em campanhas sazonais aprovadas pelo Trade Marketing. "
                    + ("A fonte descreve campanha sazonal, mas não há evidência da aprovação do Trade Marketing" if seasonal else "A fonte não caracteriza campanha sazonal")
                    + ", e a precedência entre a nota e a vedação não está definida — submeter à Diretoria Comercial (Política §11).",
                    ["desconto", "categoria"], "NORMATIVO")
            else:
                add("RB02:TETO", "RB02", "BLOCKER", f"{pct_txt(pct)} excede o teto de {pct_txt(teto)} para {cat} (Política §4).", ["desconto", "categoria"])

    def lvl_desc(v):
        return 1 if v <= 10 else 2 if v <= 20 else 3 if v <= 25 else None

    def lvl_verba(v):
        return 2 if v <= 5000 else 3 if v <= 20000 else 4

    parts, req, undef = [], 0, False
    for c in comps:
        if c["value"] is None:
            undef = True
            parts.append(c["label"] + ": valor pendente")
            continue
        lv = lvl_verba(c["value"]) if c["tipo"] == "VERBA_EXPOSICAO" else lvl_desc(c["value"])
        c["level"], c["role"] = lv, ROLES[lv] if lv else "Vedado"
        if lv is None:
            undef = True
        else:
            req = max(req, lv)
        parts.append(f"{c['label']} {c['display']} → {c['role']}")
    if pct is not None and pct > 25 and not normative:
        add("RB03:VEDADO", "RB03", "BLOCKER", "Desconto acima de 25% é vedado pela tabela de alçadas (Política §5).", ["desconto"])
    if req == 4:
        add("RB03:COMITE", "RB03", "BLOCKER", "Verba acima de R$ 20.000,00 exige Comitê Comercial, que não está entre os perfis simulados.", ["verba"], "EXTERNO")
    required = {"level": None, "role": "Indefinida enquanto houver valores pendentes ou vedados"} if (undef or not comps) else {"level": req, "role": ROLES[req]}
    if comps:
        msg = "Alçada (Política §5): " + " · ".join(parts)
        msg += (f". Componentes da mesma negociação: a separação em registros não reduz a alçada — exigida {required['role']}." if len(comps) > 1 else ".")
        add("RB03:ALCADA", "RB03", "INFO", msg, [c["key"] for c in comps])

    if verba is not None or F("verba"):
        if not contra:
            add("RB05:CONTRAPARTIDA", "RB05", "BLOCKER", "Verba de exposição sem contrapartida descrita (Política §7).", ["contrapartida"])
        if contra and not re.search(r"\d{1,2}/\d{1,2}|\d{4}-\d{2}|janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro|semana|dias|m[eê]s|meses|trimestre|per[ií]odo|durante|vig[eê]ncia", contra, re.I):
            add("RB05:PERIODO", "RB05", "WARNING",
                "A contrapartida não descreve período de execução (Política §7). Na falta dele, será considerada a vigência da condição"
                + (f" ({br(ini)} a {br(fim)})" if ini and fim else "") + "; confirme com o fornecedor se for diferente.", ["contrapartida"])
        if not lojas:
            add("RB05:LOJAS", "RB05", "BLOCKER", "Verba de exposição sem lojas identificadas (Política §7).", ["lojas"])
    if contra and "REDE" in lojas:
        m = re.search(r"\b(\d+)\s+lojas\b[^,.()]*", contra, re.I)
        if m:
            add("RB05:CONTRA_LOJAS", "RB05", "WARNING", f"A contrapartida menciona “{m.group(0).strip()}” sem identificá-las. A verificação pela Operação de Lojas (Política §7) dependerá dessa lista.", ["contrapartida"])

    substitution = None
    if forn and cat and lojas and ini:
        extrato = extrato_by_condition()
        for c in erp["condicoes"]["itens"]:
            if not (c.get("status") == "VIGENTE" and c["cod_fornecedor"] == forn and c["cod_categoria"] == cat
                    and intersects(c["lojas"], lojas) and overlap(ini, fim, c["data_inicio"], c.get("data_fim"))):
                continue
            cv = pct_txt(c["percentual"]) if c.get("percentual") is not None else brl(c.get("valor"))
            if len(comps) != 1:
                why = "proposta com mais de um componente"
            elif c["tipo"] != comps[0]["tipo"]:
                why = "tipo diferente"
            elif not same_set(c["lojas"], lojas):
                why = "conjunto de lojas diferente (não é permitido encerrar a condição vigente só em parte das lojas)"
            elif c["data_inicio"] >= ini:
                why = "condição vigente começa depois da nova"
            elif add_days(ini, -1) < business_date:
                why = "encerramento seria retroativo"
            else:
                why = None
            desc = f"{c['cod_condicao']} ({c['tipo']} {cv}, {', '.join(c['lojas'])}, {br(c['data_inicio'])}–{br(c.get('data_fim'))})"
            if not why:
                substitution = {"cod_condicao": c["cod_condicao"], "encerra_em": add_days(ini, -1), "valor_atual": cv}
                add("RB10:" + c["cod_condicao"], "RB10", "WARNING",
                    f"Sobreposição com {desc} — mesmo fornecedor, categoria e lojas (Política §9). Elegível para substituição pela regra conservadora D07: "
                    f"autorizar encerra {c['cod_condicao']} em {br(add_days(ini, -1))}, véspera do início da nova condição.", ["lojas", "inicio"])
            else:
                add("RB06:" + c["cod_condicao"], "RB06", "BLOCKER", f"Sobreposição com {desc} (Política §9). Substituição não elegível: {why}.", ["lojas", "inicio", "fim"])
            hist = extrato.get(c["cod_condicao"])
            if hist and c.get("percentual") is not None and hist.get("percentual") is not None and float(hist["percentual"]) != float(c["percentual"]):
                alt = f", alterada em {br_dt(c.get('ultima_alteracao'))} por {c['alterado_por']}" if c.get("alterado_por") else ""
                add("D05:" + c["cod_condicao"], "D05", "WARNING",
                    f"O extrato do ERP (exportado em 29/09/2026 06:00) registra {pct_txt(float(hist['percentual']))} para {c['cod_condicao']}; "
                    f"a consulta ao ERP (30/09/2026 08:15) registra {cv}{alt}. O ERP consultado prevalece como estado cadastral (D05).")
    if len(comps) > 1:
        add("RB06:COMPONENTES", "RB06", "BLOCKER",
            "Desconto e verba seriam duas condições do mesmo fornecedor, categoria, lojas e período. A política exige registros separados (§5) e proíbe "
            "coexistência para mesmo fornecedor/categoria/loja/período (§9) sem distinguir tipos. Precedência indefinida — requer interpretação formal "
            "da Diretoria Comercial (§11).", [c["key"] for c in comps], "NORMATIVO")
        add("RB14:COMPONENTES", "RB14", "INFO", f"Cada componente seria uma requisição POST /condicoes distinta ({len(comps)} operações).", [c["key"] for c in comps])
    flags = p.get("flags") or {}
    if flags.get("injection"):
        add("SEG:INSTRUCAO", "SEG", "WARNING", "A fonte contém instrução dirigida a processamento automatizado. Tratada como dado não confiável: não altera regras, aprovação nem cadastro.")
    if flags.get("approval_claim"):
        add("RB13:ALEGACAO", "RB13", "WARNING", f"A fonte afirma aprovação prévia da empresa (“{flags['approval_claim']}”). Afirmação do fornecedor não constitui evidência de aprovação.")
    src = p["source"]
    if src["kind"] != "DATASET_EMAIL":
        who = f"Exemplo editado pelo operador (derivado de {src.get('derived_from')})" if src["kind"] == "EDITED_EXAMPLE" else "Entrada manual"
        add("RB07:ORIGEM", "RB07", "WARNING", f"{who}: origem comercial não verificada; referência técnica {src['ref']}. Política §10 exige formalização por e-mail do fornecedor.")

    out.sort(key=lambda x: SEV_ORDER[x["severity"]])
    return {"findings": out, "comps": comps, "required": required, "substitution": substitution}


def actions_for(f: dict, p: dict) -> list[dict]:
    if f["severity"] == "INFO":
        return []
    if f["severity"] == "WARNING":
        a = [{"code": "ACK"}]
        if f["rule_id"] == "RB10":
            a.append({"code": "EDIT_FIELD", "field": "lojas"})
        if f["rule_id"] in ("RB05", "RB13", "RB07"):
            a.append({"code": "CONCLUDE", "option": "ESCALAR:FORNECEDOR"})
        if f["rule_id"] in ("SEG", "RB13", "RB10"):
            a.append({"code": "CONCLUDE", "option": "REJEITAR"})
        return a
    a: list[dict] = []
    if f["severity"] == "REQUIRES_CONFIRMATION":
        for k in f["affected_fields"]:
            fl = next((x for x in p["fields"] if x["key"] == k), None)
            empty = not fl or fl["value"] is None or (isinstance(fl["value"], list) and not fl["value"])
            if not empty and fl["confirmation_status"] == "PENDING":
                a.append({"code": "CONFIRM_FIELD", "field": k})
            a.append({"code": "EDIT_FIELD", "field": k})
            if empty:
                a.append({"code": "CONCLUDE", "option": "ESCALAR:FORNECEDOR"})
        return a
    for k in f["affected_fields"]:
        a.append({"code": "EDIT_FIELD", "field": k})
    if f["severity"] == "BLOCKER":
        owner = f["resolution_owner"]
        a.append({"code": "CONCLUDE", "option": "ESCALAR:CADASTRO" if owner == "EXTERNO" else "ESCALAR:DIRETORIA" if owner == "NORMATIVO" else "ESCALAR:FORNECEDOR"})
        a.append({"code": "CONCLUDE", "option": "REJEITAR"})
    return a


def display(f: dict, erp: dict) -> str | None:
    v = f.get("value")
    if v is None or (isinstance(v, list) and not v):
        return None
    ed = f["editor"]
    if ed == "supplier":
        s = next((x for x in erp["fornecedores"]["itens"] if x["cod_fornecedor"] == v), None)
        return v + (" · " + s["razao_social"] if s else "")
    if ed == "stores":
        return "REDE (todas as lojas ativas)" if "REDE" in v else ", ".join(v)
    if ed == "percent":
        return pct_txt(v)
    if ed == "money":
        return brl(v)
    if ed == "date":
        return br(v)
    return str(v)


def open_findings(p: dict) -> list[dict]:
    return [{"rule_id": f["rule_id"], "severity": f["severity"], "message": f["message"], "resolution_owner": f["resolution_owner"]}
            for f in p["findings"] if f["severity"] in ("BLOCKER", "REQUIRES_CONFIRMATION")]


def compute_state(p: dict, approval: dict | None, op: dict | None) -> str:
    if p.get("decision") and p["decision"]["type"] == "REJEITAR":
        return "REJEITADA"
    if op:
        st = op["confirmation_status"]
        if st == "CONFIRMED":
            return "CADASTRADA"
        if st == "FAILED":
            return "FALHA_REGISTRO"
        if st == "PENDING":
            return "AGUARDANDO_CONFIRMACAO"
    if p["counts"]["blockers"]:
        return "BLOQUEADA"
    if approval and approval.get("status") == "VALIDA":
        return "APROVADA"
    if p["counts"]["confirmations"] or p["counts"]["warnings_open"]:
        return "PENDENTE_REVISAO"
    return "PRONTA_PARA_APROVACAO"


def apply_conclusion(item: dict, test: dict, events: list) -> None:
    p, op = item["proposal"], item.get("operation")
    F = lambda k: next((f for f in p["fields"] if f["key"] == k), {})  # noqa: E731
    confirmed = lambda keys: all(F(k).get("confirmation_status") != "PENDING" for k in keys)  # noqa: E731
    blk = [f for f in p["findings"] if f["severity"] == "BLOCKER"]
    allow = bool(p.get("auto_allowed")) and test.get("auto_conclusion") is not False
    elig = next((f for f in blk if f["rule_id"] == "RB01"), None)
    teto = next((f for f in blk if f["id"] in ("RB02:TETO", "RB03:VEDADO")), None)
    auto = None
    if allow and elig and confirmed(elig["affected_fields"]):
        auto = {"rule": "AUTO-01", "outcome": "BLOQUEADA", "title": "Bloqueada — fornecedor inelegível", "route": "CADASTRO", "basis": elig["message"]}
    elif allow and teto and confirmed(teto["affected_fields"]):
        auto = {"rule": "AUTO-03", "outcome": "BLOQUEADA", "title": "Bloqueada — fora da política comercial", "route": None, "basis": teto["message"]}
    prev = (p.get("conclusion") or {}).get("auto")
    if auto and (not prev or prev.get("rule") != auto["rule"]):
        auto["at"] = now_iso()
        events.append({"at": now_iso(), "type": "CONCLUSAO_AUTOMATICA", "detail": auto["rule"] + " · BLOQUEADA"})
    elif auto:
        auto["at"] = prev.get("at")
    op_open = bool(op) and op["confirmation_status"] != "CONFIRMED"
    final = p["state"] in ("CADASTRADA", "REJEITADA") or op_open

    def has(sev, owner=None):
        return any(f["severity"] == sev and (not owner or f["resolution_owner"] == owner) for f in p["findings"])

    revisor_pend = any(
        (f["severity"] in ("BLOCKER", "REQUIRES_CONFIRMATION") and f["resolution_owner"] == "REVISOR")
        or any(a.get("option") == "ESCALAR:FORNECEDOR" for a in f.get("resolution_actions") or [])
        for f in p["findings"])
    ready = p["state"] in ("PRONTA_PARA_APROVACAO", "APROVADA")

    def opt(code, label, available, why, **extra):
        return {"code": code, "label": label, "available": (not final) and available,
                "unavailable_reason": "Proposta concluída ou com operação em andamento." if final else (None if available else why), **extra}

    options = [
        opt("APROVAR_CADASTRAR", "Aprovar e cadastrar no ERP simulado", ready and p["registerable"],
            "Proposta com mais de um componente." if not p["registerable"] else "Requer estado Pronta para aprovação (sem bloqueios, confirmações encerradas e avisos com ciência)."),
        opt("ESCALAR:DIRETORIA", ROUTES["DIRETORIA"], has("BLOCKER", "NORMATIVO"), "Sem pendência normativa.", type="ESCALAR", route="DIRETORIA"),
        opt("ESCALAR:CADASTRO", ROUTES["CADASTRO"], has("BLOCKER", "EXTERNO"), "Sem impedimento de cadastro do fornecedor.", type="ESCALAR", route="CADASTRO"),
        opt("ESCALAR:FORNECEDOR", ROUTES["FORNECEDOR"], revisor_pend, "Sem dado ausente ou divergente a solicitar.", type="ESCALAR", route="FORNECEDOR"),
        opt("REJEITAR", "Rejeitar proposta", True, None, type="REJEITAR"),
    ]
    rec = ("ESCALAR:CADASTRO" if has("BLOCKER", "EXTERNO") else "ESCALAR:DIRETORIA" if has("BLOCKER", "NORMATIVO")
           else "ESCALAR:FORNECEDOR" if revisor_pend else "APROVAR_CADASTRAR" if ready else None)
    revisor_open = any(f["severity"] == "REQUIRES_CONFIRMATION" or (f["severity"] == "BLOCKER" and f["resolution_owner"] == "REVISOR") for f in p["findings"])
    route = None
    if not final and not revisor_open:
        route = ("CADASTRO", "AUTO-01") if has("BLOCKER", "EXTERNO") else ("DIRETORIA", "AUTO-04") if has("BLOCKER", "NORMATIVO") else None
    if route and not p.get("decision") and allow:
        p["decision"] = {"type": "ESCALAR", "route": route[0], "automatic": True, "rule": route[1], "note": "", "actor": "Encaminhamento automático",
                         "rule_ref": route[1], "at": now_iso(), "state_at_decision": p["state"], "open_findings": open_findings(p)}
        events.append({"at": now_iso(), "type": "ENCAMINHAMENTO_AUTOMATICO", "detail": f"{route[1]} → {route[0]}"})
    p["conclusion"] = {"mode": "AUTOMATICA" if auto and not p.get("decision") else "MANUAL", "auto": auto, "recommended": rec, "options": options}


def recompute(item: dict, erp: dict, business_date: str, test: dict, events: list) -> None:
    p = item.get("proposal")
    if not p:
        return
    op, approval = item.get("operation"), item.get("approval")
    if op and op["confirmation_status"] == "CONFIRMED" and p.get("findings"):
        p["state"] = "CADASTRADA"
        return
    p["revision_hash"] = fields_hash(p["fields"])
    for f in p["fields"]:
        f["display"] = display(f, erp)
    r = validate(p, erp, business_date)
    findings = []
    for x in r["findings"]:
        if x["severity"] == "WARNING":
            x["acknowledged"] = p["acks"].get(x["id"]) == p["revision_hash"]
        findings.append(x)
    p["findings"] = findings
    for x in findings:
        x["resolution_actions"] = actions_for(x, p)
    p["components"], p["required"], p["substitution"] = r["comps"], r["required"], r["substitution"]
    p["registerable"] = len(r["comps"]) == 1
    Vd = lambda k: (next((f for f in p["fields"] if f["key"] == k), {}) or {}).get("display") or "—"  # noqa: E731
    per = Vd("inicio") + " – " + Vd("fim")
    head = " + ".join(c["label"] + " " + c["display"] for c in r["comps"]) if r["comps"] else "Condição sem componente identificado"
    p["summary"] = f"{head} · {Vd('fornecedor')} · {Vd('categoria')} · {Vd('lojas')} · {per}"
    p["impact"] = [f"Criar condição {c['tipo']} {c['display']} · {Vd('categoria')} · {Vd('lojas')} · {per}" for c in r["comps"]]
    if r["substitution"]:
        s = r["substitution"]
        p["impact"].append(f"Encerrar {s['cod_condicao']} ({s['valor_atual']}) em {br(s['encerra_em'])}, véspera do início da nova condição")
    else:
        p["impact"].append("Nenhuma condição vigente será encerrada")
    if len(r["comps"]) > 1:
        p["impact"].append(f"{len(r['comps'])} requisições POST /condicoes distintas")
    n = lambda s: [x for x in findings if x["severity"] == s]  # noqa: E731
    p["counts"] = {"blockers": len(n("BLOCKER")), "confirmations": len(n("REQUIRES_CONFIRMATION")),
                   "warnings_open": len([x for x in n("WARNING") if not x["acknowledged"]]), "warnings": len(n("WARNING"))}
    p["state"] = compute_state(p, approval, op)
    apply_conclusion(item, test, events)
