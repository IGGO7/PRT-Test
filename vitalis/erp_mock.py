"""ERP simulado — reproduz o contrato de `mock_erp/README.md` sobre o estado da sessão.

- `POST /condicoes`: Idempotency-Key UUID v4 obrigatória, replay por 24 h (relógio real),
  mesma chave + payload diferente => conflito (D14), validação de schema + semântica, fornecedor
  ativo, 1 condição por requisição, `cod_condicao_substituida` encerra a anterior na véspera.
- 429 (10 req/min por cliente) e 503 reproduzíveis e controlados (D21): o fluxo normal é estável.
- Falha "timeout após gravação" simula resposta perdida com efeito já aplicado (D16).

As consultas (fornecedores, lojas, condições) são servidas do snapshot da sessão, que parte
das respostas capturadas em 30/09/2026 08:15; somente o POST passa por limite e falhas.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker

from .dataset import condicao_post_schema, erp_snapshot, parse_iso_date

IDEMPOTENCY_WINDOW = timedelta(hours=24)
RATE_LIMIT_PER_MINUTE = 10
SimulatedFailure = Literal["503", "429", "timeout"]


class ERPTimeout(Exception):
    """A requisição pode ou não ter sido processada; nenhuma resposta chegou ao cliente."""


def initial_erp_state() -> dict[str, Any]:
    snap = erp_snapshot()
    codes = [int(c["cod_condicao"].split("-")[1]) for c in snap["condicoes"]["itens"]]
    return {
        "fornecedores": snap["fornecedores"],
        "lojas": snap["lojas"],
        "condicoes": snap["condicoes"],
        # COND-004 e COND-009 existem apenas no extrato histórico (encerradas); o próximo código é COND-011.
        "next_seq": max(codes + [10]) + 1,
        "idempotency": {},
        "post_log": [],
        "snapshot_captured_at": snap["condicoes"].get("consultado_em"),
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _error(status: int, code: str, message: str, **extra: Any) -> dict[str, Any]:
    body = {"error": code, "message": message} | extra
    return {"status_http": status, "headers": {}, "body": body}


def _is_uuid4(value: str | None) -> bool:
    try:
        return value is not None and uuid.UUID(value).version == 4
    except (ValueError, AttributeError, TypeError):
        return False


class MockERP:
    def __init__(self, erp_state: dict[str, Any], business_date: date):
        self.state = erp_state
        self.business_date = business_date

    # ------------------------------ consultas ------------------------------
    def get_fornecedores(self) -> dict[str, Any]:
        return self.state["fornecedores"]

    def get_lojas(self) -> dict[str, Any]:
        return self.state["lojas"]

    def get_condicoes(self, status: str = "VIGENTE") -> dict[str, Any]:
        items = [c for c in self.state["condicoes"]["itens"] if c.get("status") == status]
        return {"filtro": {"status": status}, "total": len(items), "itens": items}

    # ------------------------------ cadastro -------------------------------
    def _validate(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        validator = Draft202012Validator(condicao_post_schema(), format_checker=FormatChecker())
        for err in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path)):
            path = "".join(f"[{p}]" if isinstance(p, int) else (f".{p}" if i else str(p)) for i, p in enumerate(err.absolute_path))
            if err.validator == "required":
                missing = err.message.split("'")[1] if "'" in err.message else err.message
                errors.append({"campo": missing, "erro": "obrigatório"})
            else:
                errors.append({"campo": path or "(corpo)", "erro": err.message})
        tipo = payload.get("tipo")
        if tipo == "DESCONTO_PERCENTUAL":
            if "percentual" not in payload:
                errors.append({"campo": "percentual", "erro": "obrigatório para DESCONTO_PERCENTUAL"})
            if "valor" in payload:
                errors.append({"campo": "valor", "erro": "proibido em DESCONTO_PERCENTUAL"})
        elif tipo == "VERBA_EXPOSICAO":
            if "valor" not in payload:
                errors.append({"campo": "valor", "erro": "obrigatório para VERBA_EXPOSICAO"})
            if "percentual" in payload:
                errors.append({"campo": "percentual", "erro": "proibido em VERBA_EXPOSICAO"})
        stores = {l["cod_loja"] for l in self.state["lojas"]["itens"]}
        for idx, code in enumerate(payload.get("lojas") or []):
            if code != "REDE" and code not in stores:
                errors.append({"campo": f"lojas[{idx}]", "erro": f"loja inexistente: {code}"})
        if "REDE" in (payload.get("lojas") or []) and len(payload["lojas"]) > 1:
            errors.append({"campo": "lojas", "erro": "REDE deve ser usado isoladamente"})
        di, df = parse_iso_date(payload.get("data_inicio")), parse_iso_date(payload.get("data_fim"))
        if di and df and df < di:
            errors.append({"campo": "data_fim", "erro": "anterior a data_inicio"})
        sub = payload.get("cod_condicao_substituida")
        if sub and not any(c["cod_condicao"] == sub for c in self.state["condicoes"]["itens"]):
            errors.append({"campo": "cod_condicao_substituida", "erro": f"condição inexistente: {sub}"})
        return errors

    def _rate_limited(self, now: datetime) -> int | None:
        window_start = now - timedelta(seconds=60)
        recent = [t for t in self.state["post_log"] if datetime.fromisoformat(t) > window_start]
        self.state["post_log"] = recent
        if len(recent) >= RATE_LIMIT_PER_MINUTE:
            oldest = min(datetime.fromisoformat(t) for t in recent)
            return max(1, int((oldest + timedelta(seconds=60) - now).total_seconds()) + 1)
        return None

    def post_condicao(self, payload: dict[str, Any], idempotency_key: str | None, simulate: SimulatedFailure | None = None) -> dict[str, Any]:
        """Retorna {"status_http", "headers", "body"}; lança ERPTimeout quando a resposta é perdida."""
        now = _now()
        if not idempotency_key:
            return _error(400, "MISSING_IDEMPOTENCY_KEY", "Header Idempotency-Key é obrigatório")
        if not _is_uuid4(idempotency_key):
            return _error(422, "VALIDATION_ERROR", "Payload inválido", campos=[{"campo": "Idempotency-Key", "erro": "deve ser UUID v4"}])

        retry_after = self._rate_limited(now)
        if simulate == "429" or retry_after:
            resp = _error(429, "RATE_LIMIT_EXCEEDED", "Limite de requisições excedido")
            resp["headers"] = {"Retry-After": str(retry_after or 60)}
            return resp
        self.state["post_log"].append(now.isoformat())

        if simulate == "503":
            return _error(503, "SERVICE_UNAVAILABLE", "Sistema em manutenção")

        # Replay idempotente (janela de 24 h, relógio real).
        record = self.state["idempotency"].get(idempotency_key)
        if record and now - datetime.fromisoformat(record["created_at"]) <= IDEMPOTENCY_WINDOW:
            if record["payload_hash"] != _payload_hash(payload):
                return _error(422, "IDEMPOTENCY_KEY_CONFLICT", "Idempotency-Key já utilizada com payload diferente")
            replay = json.loads(json.dumps(record["response"]))
            replay["headers"] = {**replay.get("headers", {}), "Idempotent-Replay": "true"}
            if simulate == "timeout":
                raise ERPTimeout("Resposta não recebida (simulação).")
            return replay

        errors = self._validate(payload)
        if errors:
            return _error(422, "VALIDATION_ERROR", "Payload inválido", campos=errors)

        supplier = next((f for f in self.state["fornecedores"]["itens"] if f["cod_fornecedor"] == payload["cod_fornecedor"]), None)
        if supplier is None or supplier.get("status") != "ATIVO":
            return _error(422, "SUPPLIER_NOT_ACTIVE", "Fornecedor não possui cadastro ativo")

        # Grava.
        cod = f"COND-{self.state['next_seq']:03d}"
        self.state["next_seq"] += 1
        created = now.astimezone(timezone(timedelta(hours=-3))).isoformat(timespec="seconds")
        new_cond = {
            "cod_condicao": cod,
            "cod_fornecedor": payload["cod_fornecedor"],
            "cod_categoria": payload["cod_categoria"],
            "tipo": payload["tipo"],
            "lojas": list(payload["lojas"]),
            "data_inicio": payload["data_inicio"],
            "data_fim": payload["data_fim"],
            "status": "VIGENTE",
            "criado_em": created,
            "ultima_alteracao": created,
            "aprovador": payload["aprovador"],
            "origem": payload["origem"],
        }
        if "percentual" in payload:
            new_cond["percentual"] = payload["percentual"]
        if "valor" in payload:
            new_cond["valor"] = payload["valor"]
        if payload.get("contrapartida"):
            new_cond["contrapartida"] = payload["contrapartida"]
        sub = payload.get("cod_condicao_substituida")
        if sub:
            closing = (date.fromisoformat(payload["data_inicio"]) - timedelta(days=1)).isoformat()
            for c in self.state["condicoes"]["itens"]:
                if c["cod_condicao"] == sub:
                    c["data_fim"] = closing
                    c["ultima_alteracao"] = created
                    c["substituida_por"] = cod
                    c["encerrada_por"] = cod
                    if date.fromisoformat(closing) < self.business_date:
                        c["status"] = "ENCERRADA"
            new_cond["cod_condicao_substituida"] = sub
        self.state["condicoes"]["itens"].append(new_cond)
        self.state["condicoes"]["total"] = len(self.state["condicoes"]["itens"])

        response = {"status_http": 201, "headers": {}, "body": {"cod_condicao": cod, "status": "VIGENTE", "criado_em": created}}
        self.state["idempotency"][idempotency_key] = {
            "payload_hash": _payload_hash(payload),
            "response": response,
            "created_at": now.isoformat(),
        }
        if simulate == "timeout":
            raise ERPTimeout("Resposta não recebida após o processamento (simulação).")
        return response
