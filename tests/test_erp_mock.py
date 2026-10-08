"""Contrato do ERP simulado (mock_erp/README.md + schema)."""

from __future__ import annotations

import copy
import uuid
from datetime import date

from vitalis.erp_mock import ERPTimeout, MockERP, initial_erp_state

BASE = {
    "cod_fornecedor": "FORN-006", "cod_categoria": "MIP", "tipo": "DESCONTO_PERCENTUAL", "lojas": ["LOJA-120", "LOJA-121"],
    "percentual": 9.5, "data_inicio": "2026-11-01", "data_fim": "2027-01-31",
    "aprovador": {"matricula": "004512", "nome": "Fulano de Tal"}, "origem": {"tipo": "EMAIL", "referencia": "<x@y>"},
}


def erp():
    return MockERP(initial_erp_state(), date(2026, 9, 30))


def test_next_code_and_201():
    e = erp()
    resp = e.post_condicao(copy.deepcopy(BASE), str(uuid.uuid4()))
    assert resp["status_http"] == 201 and resp["body"]["cod_condicao"] == "COND-011"


def test_missing_key_400():
    assert erp().post_condicao(BASE, None)["body"]["error"] == "MISSING_IDEMPOTENCY_KEY"


def test_validation_422_lists_fields():
    bad = copy.deepcopy(BASE)
    del bad["data_fim"]
    bad["lojas"] = ["LOJA-120", "LOJA-999"]
    bad["valor"] = 10
    body = erp().post_condicao(bad, str(uuid.uuid4()))["body"]
    campos = {c["campo"] for c in body["campos"]}
    assert body["error"] == "VALIDATION_ERROR" and {"data_fim", "lojas[1]", "valor"} <= campos


def test_inactive_supplier_422():
    p = copy.deepcopy(BASE) | {"cod_fornecedor": "FORN-005", "cod_categoria": "SUPLEMENTOS"}
    assert erp().post_condicao(p, str(uuid.uuid4()))["body"]["error"] == "SUPPLIER_NOT_ACTIVE"


def test_idempotent_replay_and_conflict():
    e, key = erp(), str(uuid.uuid4())
    first = e.post_condicao(copy.deepcopy(BASE), key)
    replay = e.post_condicao(copy.deepcopy(BASE), key)
    assert replay["body"] == first["body"] and replay["headers"]["Idempotent-Replay"] == "true"
    conflict = e.post_condicao(copy.deepcopy(BASE) | {"percentual": 9.0}, key)
    assert conflict["status_http"] == 422 and conflict["body"]["error"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert sum(1 for c in e.state["condicoes"]["itens"] if c["cod_fornecedor"] == "FORN-006") == 2  # COND-005 + 1 nova


def test_timeout_after_commit_then_replay():
    e, key = erp(), str(uuid.uuid4())
    try:
        e.post_condicao(copy.deepcopy(BASE), key, simulate="timeout")
        raise AssertionError("deveria lançar timeout")
    except ERPTimeout:
        pass
    assert e.post_condicao(copy.deepcopy(BASE), key)["headers"]["Idempotent-Replay"] == "true"


def test_rate_limit_429_with_retry_after():
    e = erp()
    for _ in range(10):
        e.post_condicao(copy.deepcopy(BASE), str(uuid.uuid4()), simulate="503")
    resp = e.post_condicao(copy.deepcopy(BASE), str(uuid.uuid4()))
    assert resp["status_http"] == 429 and int(resp["headers"]["Retry-After"]) >= 1


def test_substitution_closes_previous_on_eve():
    e = erp()
    p = copy.deepcopy(BASE) | {"cod_fornecedor": "FORN-002", "cod_categoria": "HIGIENE_BELEZA", "lojas": ["LOJA-104", "LOJA-107", "LOJA-112"],
                               "percentual": 13.0, "data_inicio": "2026-10-01", "data_fim": "2027-03-31", "cod_condicao_substituida": "COND-003"}
    assert e.post_condicao(p, str(uuid.uuid4()))["status_http"] == 201
    cond3 = next(c for c in e.state["condicoes"]["itens"] if c["cod_condicao"] == "COND-003")
    assert cond3["data_fim"] == "2026-09-30" and cond3["substituida_por"] == "COND-011"
