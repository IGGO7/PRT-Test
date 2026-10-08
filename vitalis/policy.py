"""POL-COM-004 v3.2 convertida manualmente em configuração versionada (ADR-06, sem RAG).

Cada regra mantém referência à seção do documento original. Ambiguidades da política
NÃO são resolvidas aqui: ficam declaradas em `NORMATIVE_GAPS` e o motor de regras as trata
de forma conservadora (bloqueio/escalonamento), conforme RB13/RB16.
"""

from __future__ import annotations

from dataclasses import dataclass

POLICY_ID = "POL-COM-004"
POLICY_VERSION = "3.2"
POLICY_REF = f"{POLICY_ID} v{POLICY_VERSION}"
RULESET_VERSION = "rules-2026.10.08"  # versão da tradução da política em código

CATEGORIES: dict[str, str] = {
    "GENERICOS": "Genéricos",
    "MEDICAMENTOS_REFERENCIA": "Medicamentos de referência",
    "MIP": "Medicamentos isentos de prescrição (MIP)",
    "HIGIENE_BELEZA": "Higiene e beleza",
    "DERMOCOSMETICOS": "Dermocosméticos",
    "SUPLEMENTOS": "Suplementos",
}

# §4 — teto ordinário de desconto por categoria (percentual).
DISCOUNT_CEILING: dict[str, float] = {
    "GENERICOS": 15.0,
    "MEDICAMENTOS_REFERENCIA": 12.0,
    "MIP": 18.0,
    "HIGIENE_BELEZA": 20.0,
    "DERMOCOSMETICOS": 20.0,
    "SUPLEMENTOS": 20.0,
}

# Nota (*) da §4: campanhas sazonais aprovadas pelo Trade Marketing — até 30% em GENERICOS e MIP.
SEASONAL_CATEGORIES: frozenset[str] = frozenset({"GENERICOS", "MIP"})
SEASONAL_MAX: float = 30.0

# §5 — vedação geral de desconto acima de 25%.
DISCOUNT_FORBIDDEN_ABOVE: float = 25.0

# §6 — vigência.
MAX_VALIDITY_MONTHS: int = 12


@dataclass(frozen=True)
class Authority:
    level: int
    code: str
    label: str


COORDENACAO = Authority(1, "COORDENACAO_COMERCIAL", "Coordenação Comercial")
GERENCIA = Authority(2, "GERENCIA_COMERCIAL", "Gerência Comercial")
DIRETORIA = Authority(3, "DIRETORIA_COMERCIAL", "Diretoria Comercial")
COMITE = Authority(4, "COMITE_COMERCIAL", "Comitê Comercial")
AUTHORITIES: dict[str, Authority] = {a.code: a for a in (COORDENACAO, GERENCIA, DIRETORIA, COMITE)}


def discount_authority(percentual: float) -> Authority | None:
    """§5 — alçada para desconto percentual. `None` = vedado (acima de 25%)."""
    if percentual <= 10.0:
        return COORDENACAO
    if percentual <= 20.0:
        return GERENCIA
    if percentual <= DISCOUNT_FORBIDDEN_ABOVE:
        return DIRETORIA
    return None


def verba_authority(valor_total: float) -> Authority:
    """§5 — alçada para verba de exposição, sobre o valor TOTAL negociado (D11)."""
    if valor_total <= 5_000.0:
        return GERENCIA
    if valor_total <= 20_000.0:
        return DIRETORIA
    return COMITE


# Aprovadores SIMULADOS e declarados (D10). Não representam identidade corporativa real.
SIMULATED_APPROVERS: list[dict[str, str | int]] = [
    {"approver_id": "coord", "matricula": "SIM-004101", "nome": "Paula Ribeiro (simulada)",
     "role": COORDENACAO.code, "role_label": COORDENACAO.label, "level": COORDENACAO.level},
    {"approver_id": "gerencia", "matricula": "SIM-004215", "nome": "Eduardo Lima (simulado)",
     "role": GERENCIA.code, "role_label": GERENCIA.label, "level": GERENCIA.level},
    {"approver_id": "diretoria", "matricula": "SIM-003012", "nome": "Helena Costa (simulada)",
     "role": DIRETORIA.code, "role_label": DIRETORIA.label, "level": DIRETORIA.level},
    {"approver_id": "comite", "matricula": "SIM-COMITE", "nome": "Comitê Comercial (simulado)",
     "role": COMITE.code, "role_label": COMITE.label, "level": COMITE.level},
]


def get_approver(approver_id: str) -> dict[str, str | int] | None:
    return next((a for a in SIMULATED_APPROVERS if a["approver_id"] == approver_id), None)


# Lacunas normativas declaradas (§16 R1/R2; §23 "Regras não determinadas pelas fontes").
NORMATIVE_GAPS: dict[str, str] = {
    "SEASONAL_PRECEDENCE": (
        "A nota (*) da §4 admite até 30% em campanhas sazonais de GENERICOS/MIP aprovadas pelo Trade "
        "Marketing, mas a §5 veda descontos acima de 25%. A política não define precedência nem a alçada "
        "da exceção; casos não previstos vão à Diretoria Comercial (§11)."
    ),
    "TYPE_COEXISTENCE": (
        "A §5 exige cadastrar desconto e verba de uma mesma negociação como condições próprias, mas a §9 "
        "proíbe duas condições vigentes para o mesmo fornecedor, categoria, loja e período sem distinguir "
        "tipos. A coexistência não está definida; casos não previstos vão à Diretoria Comercial (§11)."
    ),
}
