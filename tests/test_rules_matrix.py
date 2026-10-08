"""Matriz das regras da POL-COM-004 v3.2 sobre o motor determinístico (sem IA).

Cada caso parte de uma proposta limpa (sem apontamentos além da alçada) e altera um único
aspecto, verificando os limites exatos da política: tetos por categoria, faixas de alçada,
vigência, contrapartida, fornecedor, sobreposição/substituição, componentes múltiplos,
exceção sazonal e sinais de segurança.
"""

from __future__ import annotations

import copy

import pytest

from vitalis.engine import recompute, validate
from vitalis.erp_mock import initial_erp_state
from vitalis.policy import DISCOUNT_CEILING

BD = "2026-09-30"
ERP = initial_erp_state()


def proposal(flags=None, kind="DATASET_EMAIL", **over):
    vals = {"fornecedor": "FORN-003", "categoria": "DERMOCOSMETICOS", "lojas": ["LOJA-101"], "inicio": "2026-10-15",
            "fim": "2026-12-31", "contrapartida": None, "desconto": 10.0}
    vals.update(over)
    fields = []
    for k, v in vals.items():
        if v is ... or (k in ("desconto", "verba") and v is None and k not in over):
            continue
        fields.append({"key": k, "label": k, "editor": "text", "value": v, "origin_kind": "EXPLICIT",
                       "confirmation_status": "NOT_REQUIRED", "raw_value": str(v), "reason": None})
    return {"fields": fields, "flags": flags or {}, "acks": {},
            "source": {"kind": kind, "ref": "SIM-x", "derived_from": None}}


def ids(p):
    return {f["id"] for f in validate(p, ERP, BD)["findings"]}


def sev(p, fid):
    return next(f for f in validate(p, ERP, BD)["findings"] if f["id"] == fid)["severity"]


def required(p):
    return validate(p, ERP, BD)["required"]


def test_baseline_clean():
    assert ids(proposal()) == {"RB03:ALCADA"}


# ---------------------------------------------------------------- §4 tetos por categoria
@pytest.mark.parametrize("cat,teto", list(DISCOUNT_CEILING.items()))
def test_ceiling_boundary(cat, teto):
    assert "RB02:TETO" not in ids(proposal(categoria=cat, desconto=teto))
    assert "RB02:TETO" in ids(proposal(categoria=cat, desconto=round(teto + 0.5, 2)))


# ---------------------------------------------------------------- §5 alçadas
@pytest.mark.parametrize("pct,level", [(1, 1), (10, 1), (10.01, 2), (20, 2)])
def test_discount_authority(pct, level):
    assert required(proposal(categoria="HIGIENE_BELEZA", desconto=pct))["level"] == level


def test_discount_above_20_needs_board_and_above_25_forbidden():
    # 20,01–25% só é possível em categoria cujo teto permita; nenhuma tem teto > 20, então vira RB02 + alçada Diretoria
    p = proposal(categoria="HIGIENE_BELEZA", desconto=22)
    assert "RB02:TETO" in ids(p) and required(p)["level"] == 3
    p = proposal(categoria="HIGIENE_BELEZA", desconto=26)
    assert {"RB02:TETO", "RB03:VEDADO"} <= ids(p) and required(p)["level"] is None


@pytest.mark.parametrize("valor,level,comite", [(5000, 2, False), (5000.01, 3, False), (20000, 3, False), (20000.01, 4, True)])
def test_verba_authority(valor, level, comite):
    p = proposal(desconto=..., verba=valor, contrapartida="Ilha promocional durante a vigência")
    assert required(p)["level"] == level
    assert ("RB03:COMITE" in ids(p)) is comite


# ---------------------------------------------------------------- §6 vigência
@pytest.mark.parametrize("over,fid", [
    ({"inicio": None}, "RB04:INICIO"),
    ({"fim": None}, "RB04:FIM"),
    ({"inicio": "2026-09-29"}, "RB04:RETRO"),
    ({"inicio": "2026-11-01", "fim": "2026-10-31"}, "RB04:ORDEM"),
    ({"inicio": "2026-10-01", "fim": "2027-10-01"}, "RB04:DURACAO"),
])
def test_validity_rules(over, fid):
    assert fid in ids(proposal(**over)) and sev(proposal(**over), fid) == "BLOCKER"


def test_validity_limits_allowed():
    assert ids(proposal(inicio=BD, fim="2027-09-29")) == {"RB03:ALCADA"}          # início = data de negócio; 12 meses exatos
    assert "RB04:DURACAO" in ids(proposal(inicio=BD, fim="2027-09-30"))            # 12 meses + 1 dia


# ---------------------------------------------------------------- §7 contrapartida
def test_verba_requires_counterpart_period_and_stores():
    base = {"desconto": ..., "verba": 3000.0}
    assert "RB05:CONTRAPARTIDA" in ids(proposal(**base))
    assert sev(proposal(**base, contrapartida="Ilha promocional"), "RB05:PERIODO") == "BLOCKER"
    assert "RB05:PERIODO" not in ids(proposal(**base, contrapartida="Ilha promocional de 15/10 a 31/12"))
    assert "RB05:LOJAS" in ids(proposal(**base, contrapartida="Ilha durante a vigência", lojas=[]))


def test_counterpart_vague_store_list_on_network():
    p = proposal(lojas=["REDE"], contrapartida="Ponta de gôndola nas 10 lojas de maior giro durante a campanha")
    assert "RB05:CONTRA_LOJAS" in ids(p)


# ---------------------------------------------------------------- §8 fornecedor
def test_supplier_inactive_or_unknown():
    assert sev(proposal(fornecedor="FORN-005"), "RB01:FORN-005") == "BLOCKER"
    assert "RB01:FORN-999" in ids(proposal(fornecedor="FORN-999"))


# ---------------------------------------------------------------- §9 sobreposição e substituição (D07)
def beta(**over):
    v = {"fornecedor": "FORN-002", "categoria": "HIGIENE_BELEZA", "lojas": ["LOJA-104", "LOJA-107", "LOJA-112"],
         "inicio": "2026-10-01", "fim": "2027-03-31", "desconto": 13.0}
    v.update(over)
    return proposal(**v)


def test_substitution_eligible():
    r = validate(beta(), ERP, BD)
    assert "RB10:COND-003" in {f["id"] for f in r["findings"]} and r["substitution"]["encerra_em"] == "2026-09-30"


@pytest.mark.parametrize("over", [{"lojas": ["LOJA-104", "LOJA-107"]}, {"lojas": ["REDE"]}])
def test_partial_or_network_overlap_blocks(over):
    assert "RB06:COND-003" in ids(beta(**over))


def test_no_overlap_other_stores_or_after_end():
    assert not any(i.startswith(("RB06", "RB10")) for i in ids(beta(lojas=["LOJA-101"])))
    assert not any(i.startswith(("RB06", "RB10")) for i in ids(beta(inicio="2027-01-01", fim="2027-06-30")))


def test_network_condition_intersects_single_store():
    # COND-006 (FORN-007, HIGIENE_BELEZA, REDE) colide com qualquer loja do mesmo fornecedor/categoria
    p = proposal(fornecedor="FORN-007", categoria="HIGIENE_BELEZA", lojas=["LOJA-140"], inicio="2026-10-01", fim="2026-11-15", desconto=9)
    assert "RB06:COND-006" in ids(p)


def test_different_type_overlap_not_substitutable():
    p = proposal(fornecedor="FORN-006", categoria="MIP", lojas=["LOJA-120", "LOJA-121"], inicio="2026-10-01", fim="2026-12-31", desconto=5)
    assert "RB06:COND-005" in ids(p)


# ---------------------------------------------------------------- §5 + §9 componentes múltiplos (Gama)
def test_mixed_components_normative_block():
    p = proposal(verba=8000.0, contrapartida="Ilha promocional durante a vigência")
    r = validate(p, ERP, BD)
    f = next(x for x in r["findings"] if x["id"] == "RB06:COMPONENTES")
    assert f["severity"] == "BLOCKER" and f["resolution_owner"] == "NORMATIVO" and r["required"]["level"] == 3


def test_no_component():
    assert "RB08:SEM_COMPONENTE" in ids(proposal(desconto=...))


# ---------------------------------------------------------------- nota (*) sazonal
@pytest.mark.parametrize("cat,pct,seasonal,expect", [
    ("GENERICOS", 28, "Campanha Fim de Ano", "RB13:SAZONAL"),    # Alfa: exceção invocada, >25% → precedência indefinida
    ("GENERICOS", 22, "campanha de inverno", "RB13:SAZONAL"),    # exceção invocada, critérios de aprovação indefinidos
    ("MIP", 29, "campanha sazonal", "RB13:SAZONAL"),
    ("GENERICOS", 28, None, "RB02:TETO"),                        # sem campanha: a exceção não se aplica
    ("GENERICOS", 31, "campanha sazonal", "RB02:TETO"),          # acima de 30% nem a exceção admite
    ("SUPLEMENTOS", 24, "campanha sazonal", "RB02:TETO"),        # categoria fora da exceção
])
def test_seasonal_exception(cat, pct, seasonal, expect):
    p = proposal(categoria=cat, desconto=pct, flags={"seasonal": seasonal} if seasonal else {})
    found = ids(p)
    assert expect in found
    other = "RB02:TETO" if expect == "RB13:SAZONAL" else "RB13:SAZONAL"
    assert other not in found
    assert ("RB03:VEDADO" in found) == (pct > 25 and expect == "RB02:TETO")


def test_seasonal_message_matches_case():
    m28 = next(f["message"] for f in validate(proposal(categoria="GENERICOS", desconto=28, flags={"seasonal": "x"}), ERP, BD)["findings"] if f["id"] == "RB13:SAZONAL")
    m22 = next(f["message"] for f in validate(proposal(categoria="GENERICOS", desconto=22, flags={"seasonal": "x"}), ERP, BD)["findings"] if f["id"] == "RB13:SAZONAL")
    assert "25%" in m28 and "precedência" in m28
    assert "precedência" not in m22 and "critérios" in m22


# ---------------------------------------------------------------- segurança e origem
def test_security_signals_and_origin():
    found = ids(proposal(flags={"injection": "x", "approval_claim": "y"}, kind="MANUAL_SIMULATION"))
    assert {"SEG:INSTRUCAO", "RB13:ALEGACAO", "RB07:ORIGEM"} <= found


# ---------------------------------------------------------------- estado e conclusão automática
def run(p):
    item = {"proposal": copy.deepcopy(p), "approval": None, "operation": None}
    item["proposal"]["auto_allowed"] = True
    recompute(item, copy.deepcopy(ERP), BD, {"auto_conclusion": True}, [])
    return item["proposal"]


def test_auto_conclusions():
    assert run(proposal())["state"] == "PRONTA_PARA_APROVACAO"
    p = run(proposal(fornecedor="FORN-005", categoria="SUPLEMENTOS"))
    assert p["decision"]["rule"] == "AUTO-01" and p["decision"]["route"] == "CADASTRO"
    p = run(proposal(categoria="GENERICOS", desconto=28, flags={"seasonal": "Campanha Fim de Ano"}))
    assert p["decision"]["rule"] == "AUTO-04" and p["decision"]["route"] == "DIRETORIA"
    p = run(proposal(categoria="GENERICOS", desconto=28))
    assert p["conclusion"]["auto"]["rule"] == "AUTO-03" and p["state"] == "BLOQUEADA" and not p.get("decision")


def test_warning_requires_ack_before_approval():
    p = run(beta())
    warns = {f["id"] for f in p["findings"] if f["severity"] == "WARNING"}
    assert warns == {"RB10:COND-003", "D05:COND-003"}                       # substituição + divergência extrato × ERP
    assert p["state"] == "PENDENTE_REVISAO" and p["counts"]["warnings_open"] == 2
    p["acks"] = {w: p["revision_hash"] for w in warns}
    assert run(p)["state"] == "PRONTA_PARA_APROVACAO"


def test_pending_confirmation_blocks_progress():
    p = proposal()
    p["fields"][1]["confirmation_status"] = "PENDING"
    assert sev(p, "RB16:categoria") == "REQUIRES_CONFIRMATION"
    assert run(p)["state"] == "PENDENTE_REVISAO"
