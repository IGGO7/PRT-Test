"""Leitura das fontes originais do dataset (imutáveis, versionadas no repositório — ADR-07)."""

from __future__ import annotations

import copy
import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from email import policy as email_policy
from email.parser import BytesParser
from functools import lru_cache
from typing import Any

from .config import DATASET_DIR

EMAILS_DIR = DATASET_DIR / "emails"
ATTACHMENTS_DIR = EMAILS_DIR / "anexos"
ERP_DIR = DATASET_DIR / "mock_erp"

# Catálogo de exemplos carregáveis (D01/D02): fixtures de teste, NÃO fila operacional.
EXAMPLE_FILES: dict[str, str] = {
    "beta": "2026-09-29_1005_beta.eml",
    "gama": "2026-09-28_1640_gama.eml",
    "alfa": "2026-09-28_0912_alfa.eml",
    "nutrivida": "2026-09-29_1830_nutrivida.eml",
}
EXAMPLE_LABELS: dict[str, str] = {
    "beta": "Beta — HPC Tijuca/Méier/Botafogo",
    "gama": "Gama — Proposta Q4 com anexo CSV",
    "alfa": "Alfa — Campanha sazonal de genéricos",
    "nutrivida": "Nutrivida — Renovação de suplementos",
}

_ATTACHMENT_MARKER = re.compile(r"\[Anexo:\s*([^\]\s]+)\s*\]", re.IGNORECASE)


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"filename": self.filename, "content": self.content}


@dataclass(frozen=True)
class ExampleFixture:
    example_id: str
    label: str
    file_name: str
    message_id: str
    in_reply_to: str | None
    sender: str
    to: str
    cc: str | None
    date_header: str | None
    subject: str
    sent_at: str | None
    body: str
    attachment: Attachment | None

    def public_dict(self) -> dict[str, Any]:
        return {
            "example_id": self.example_id,
            "label": self.label,
            "file_name": self.file_name,
            "message_id": self.message_id,
            "sender": self.sender,
            "to": self.to,
            "subject": self.subject,
            "sent_at": self.sent_at,
            "body": self.body,
            "attachment": self.attachment.to_dict() if self.attachment else None,
        }


def _read_text(path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_eml(example_id: str, file_name: str) -> ExampleFixture:
    raw = (EMAILS_DIR / file_name).read_bytes()
    msg = BytesParser(policy=email_policy.default).parsebytes(raw)
    body_part = msg.get_body(preferencelist=("plain",))
    body = body_part.get_content() if body_part is not None else ""
    sent_at = None
    if msg["Date"]:
        try:
            sent_at = msg["Date"].datetime.isoformat()
        except Exception:  # pragma: no cover - cabeçalho malformado
            sent_at = str(msg["Date"])
    attachment = None
    marker = _ATTACHMENT_MARKER.search(body)
    if marker:
        att_path = ATTACHMENTS_DIR / marker.group(1)
        if att_path.exists():
            attachment = Attachment(filename=marker.group(1), content=_read_text(att_path))
    return ExampleFixture(
        example_id=example_id,
        label=EXAMPLE_LABELS[example_id],
        file_name=file_name,
        message_id=str(msg["Message-ID"] or "").strip(),
        in_reply_to=(str(msg["In-Reply-To"]).strip() if msg["In-Reply-To"] else None),
        sender=str(msg["From"] or "").strip(),
        to=str(msg["To"] or "").strip(),
        cc=(str(msg["Cc"]).strip() if msg["Cc"] else None),
        date_header=(str(msg["Date"]).strip() if msg["Date"] else None),
        subject=str(msg["Subject"] or "").strip(),
        sent_at=sent_at,
        body=body,
        attachment=attachment,
    )


@lru_cache(maxsize=1)
def load_examples() -> dict[str, ExampleFixture]:
    return {eid: _parse_eml(eid, fname) for eid, fname in EXAMPLE_FILES.items()}


def get_example(example_id: str) -> ExampleFixture | None:
    return load_examples().get(example_id)


@lru_cache(maxsize=1)
def _erp_snapshot_raw() -> dict[str, Any]:
    resp = ERP_DIR / "respostas"
    return {
        "fornecedores": json.loads(_read_text(resp / "GET_fornecedores.json")),
        "lojas": json.loads(_read_text(resp / "GET_lojas.json")),
        "condicoes": json.loads(_read_text(resp / "GET_condicoes.json")),
    }


def erp_snapshot() -> dict[str, Any]:
    """Cópia profunda do snapshot do ERP consultado em 30/09/2026 08:15 (estado inicial de cada sessão)."""
    return copy.deepcopy(_erp_snapshot_raw())


@lru_cache(maxsize=1)
def condicao_post_schema() -> dict[str, Any]:
    return json.loads(_read_text(ERP_DIR / "schema" / "condicao_post.schema.json"))


def _br_decimal(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    return float(value.replace(".", "").replace(",", "."))


def _yyyymmdd(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%Y%m%d").date().isoformat()


@lru_cache(maxsize=1)
def _extrato_rows() -> tuple[dict[str, Any], ...]:
    """Extrato histórico do ERP (29/09/2026 06:00). Referência e evidência de divergência (D05), nunca estado vigente."""
    text = (DATASET_DIR / "extrato_erp.csv").read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    rows = []
    for r in reader:
        rows.append(
            {
                "cod_condicao": r["COND_ID"],
                "cod_fornecedor": r["COD_FORN"],
                "nome_fornecedor": r["NOME_FORN"],
                "cod_categoria": r["CATEG"],
                "cod_loja": r["COD_LOJA"],
                "tipo": "DESCONTO_PERCENTUAL" if r["TP_COND"] == "DESC" else "VERBA_EXPOSICAO",
                "percentual": _br_decimal(r["VLR_PCT"]),
                "valor": _br_decimal(r["VLR_BRL"]),
                "data_inicio": _yyyymmdd(r["DT_INI"]),
                "data_fim": _yyyymmdd(r["DT_FIM"]),
                "status": r["STATUS"],
                "data_cadastro": _yyyymmdd(r["DT_CAD"]),
                "usuario_cadastro": r["USR_CAD"],
            }
        )
    return tuple(rows)


def extrato_by_condition() -> dict[str, dict[str, Any]]:
    """Agrega o extrato (condição × loja) em uma visão por condição."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in _extrato_rows():
        cond = grouped.setdefault(
            row["cod_condicao"],
            {k: v for k, v in row.items() if k != "cod_loja"} | {"lojas": []},
        )
        cond["lojas"].append(row["cod_loja"])
    return copy.deepcopy(grouped)


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
