"""Converte a saída estruturada do agente de IA no formato `fields[]` da interface (handoff §3).

Tudo aqui é determinístico: validação de códigos contra o cadastro, normalização de números e
datas, localização literal das evidências na fonte e definição do que exige verificação humana
(D06). O modelo sugere; este módulo decide o que é tratado como certo e o que é suposto.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .engine import EDITORS, LABELS, brl, pct_txt
from .extraction import LLMExtraction, LLMField, LLMQuote, parse_date, parse_number
from .policy import CATEGORIES

_LOC = {"corpo": "Corpo do e-mail", "assunto": "Assunto", "remetente": "Cabeçalho From", "anexo": "Anexo"}
_INJECTION = [re.compile(r"\[[^\]]*(processamento|sistema|automat)[^\]]*\]", re.I | re.S),
              re.compile(r"(desconsidere|ignore|ignorar)[^.]{0,80}(regras|valida)[^.]*\.", re.I | re.S)]
_CLAIM = re.compile(r"[^.\n\[]*(pr[eé]-?aprovad|j[aá] (foi )?aprovad)[^.\n]*", re.I)
_CAMPAIGN = re.compile(r"campanha [^\n.,]*", re.I)
_THREAD = re.compile(r"-{3,}\s*mensagem original", re.I)


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c)).casefold()


def exact_span(haystack: str, quote: str) -> str | None:
    """Devolve o trecho ORIGINAL da fonte que corresponde à citação (tolerando quebras de linha/espaços).
    Nunca devolve texto sintético: se não houver correspondência literal, retorna None."""
    q = (quote or "").strip()
    if not q or not haystack:
        return None
    if q in haystack:
        return q
    pattern = r"\s+".join(re.escape(tok) for tok in q.split())
    m = re.search(pattern, haystack, re.I)
    return m.group(0) if m else None


class _Ctx:
    def __init__(self, source: dict):
        att = source.get("attachment") or {}
        self.body = source.get("body") or ""
        self.texts = {"corpo": self.body, "assunto": source.get("subject") or "", "remetente": source.get("from") or "",
                      "anexo": att.get("content") or ""}
        self.att_name = att.get("name")
        self.cells = {c.strip() for line in self.texts["anexo"].splitlines() for c in line.split(";") if c.strip()}
        self.evidence: list[dict] = []
        self.csv_hl: list[str] = []

    def locate(self, q: LLMQuote) -> tuple[str | None, str | None]:
        """(trecho literal, fonte onde foi encontrado). Procura primeiro na fonte indicada pelo modelo."""
        order = [q.source] + [s for s in self.texts if s != q.source]
        for src in order:
            span = exact_span(self.texts[src], q.quote)
            if span:
                return span, src
        return None, None

    def register(self, quotes: list[LLMQuote], field: str | None, kind: str = "field") -> tuple[list[str], set[str]]:
        found, sources = [], set()
        for q in quotes:
            span, src = self.locate(q)
            if not span:
                continue
            found.append(span)
            sources.add(src)
            if src == "corpo" and not any(e["text"] == span for e in self.evidence):
                self.evidence.append({"text": span, "field": field, "kind": kind})
            if src == "anexo":
                for tok in re.split(r"[;\n]", span):
                    tok = tok.strip()
                    if tok in self.cells and tok not in self.csv_hl:
                        self.csv_hl.append(tok)
        return found, sources

    def location(self, sources: set[str]) -> str:
        if not sources:
            return "Nenhuma fonte"
        names = [(_LOC[s] + (f" {self.att_name}" if s == "anexo" and self.att_name else "")) for s in ("corpo", "assunto", "remetente", "anexo") if s in sources]
        return " × ".join(names)


def _field(key: str, value: Any, raw: str | None, location: str, origin: str, status: str, reason: str | None) -> dict:
    return {"key": key, "label": LABELS[key], "editor": EDITORS[key], "value": value, "raw_value": raw or "não informado",
            "source_location": location, "origin_kind": origin, "confirmation_status": status, "reason": reason}


def _simple(ctx: _Ctx, key: str, f: LLMField, parser, always_confirm: bool, validator=None) -> dict:
    spans, sources = ctx.register(f.evidence, key)
    alt_txt = []
    for alt in f.alternatives:
        a_spans, a_src = ctx.register(alt.evidence, key)
        sources |= a_src
        alt_txt.append(f"{ctx.location(a_src) if a_src else 'fonte'}: {alt.value}")
    origin = f.kind
    value = parser(f.value) if (f.value is not None and parser) else f.value
    reason = f.note
    if origin == "CONFLICT":
        value = None
    if value is not None and validator:
        ok, why = validator(value)
        if not ok:
            value, origin, reason = None, "MISSING", why
    if value is None and origin not in ("CONFLICT", "MISSING"):
        origin, reason = "MISSING", reason or "Valor não pôde ser normalizado."
    if origin == "EXPLICIT" and not spans:
        origin, reason = "INFERRED", "Evidência literal não localizada na fonte; tratada como inferência."
    raw = " · ".join(alt_txt) if origin == "CONFLICT" and alt_txt else (" · ".join(spans) or f.value)
    needs = always_confirm or origin != "EXPLICIT"
    status = "PENDING" if needs else "NOT_REQUIRED"
    if status == "PENDING" and not reason:
        reason = "Não informado em nenhuma fonte" if origin == "MISSING" else "valor proposto pela interpretação"
    return _field(key, value, raw, ctx.location(sources), origin, status, reason)


def to_fields(llm: LLMExtraction, source: dict, erp: dict) -> dict:
    ctx = _Ctx(source)
    supplier_codes = {f["cod_fornecedor"] for f in erp["fornecedores"]["itens"]}
    lojas = {l["cod_loja"] for l in erp["lojas"]["itens"]}
    active_stores = sum(1 for l in erp["lojas"]["itens"] if l.get("status") == "ATIVA")
    fields: list[dict] = []

    # Fornecedor, categoria e lojas são correspondências com o cadastro: sempre exigem confirmação humana.
    forn = _simple(ctx, "fornecedor", llm.supplier_code, lambda v: v.strip().upper(), True,
                   lambda v: (v in supplier_codes, f"Código {v} não existe no cadastro simulado"))
    if llm.supplier_name.value:
        forn["raw_value"] = llm.supplier_name.value + ((" · " + forn["raw_value"]) if forn["raw_value"] not in ("não informado", llm.supplier_name.value) else "")
        ctx.register(llm.supplier_name.evidence, "fornecedor")
    if forn["value"] is None and forn["origin_kind"] == "MISSING":
        forn["reason"] = forn["reason"] or "Sem correspondência no cadastro simulado"
    fields.append(forn)
    fields.append(_simple(ctx, "categoria", llm.category_code, lambda v: v.strip().upper(), True,
                          lambda v: (v in CATEGORIES, f"Categoria {v} não existe no ERP")))

    # Lojas
    mentions, codes, missing, sources = [], [], [], set()
    for s in llm.stores:
        spans, src = ctx.register(s.evidence, "lojas")
        sources |= src
        mentions.append(s.mention)
        code = (s.cod_loja or "").strip().upper() or None
        if code and code in lojas:
            codes.append(code)
        elif not llm.applies_to_all_stores:
            missing.append(s.mention)
    if llm.applies_to_all_stores:
        f_lojas = _field("lojas", ["REDE"], ", ".join(mentions) or "toda a rede", ctx.location(sources), "NORMALIZED", "PENDING", "Expressão de abrangência total")
    elif codes:
        why = ("Sem correspondência: " + ", ".join(missing)) if missing else "Nomes de unidade associados aos códigos de loja"
        f_lojas = _field("lojas", sorted(set(codes)), ", ".join(mentions), ctx.location(sources), "NORMALIZED", "PENDING", why)
    else:
        f_lojas = _field("lojas", None, ", ".join(mentions) or "não identificadas", ctx.location(sources), "MISSING", "PENDING", "Nenhuma loja identificada")
    fields.append(f_lojas)

    fields.append(_simple(ctx, "inicio", llm.start_date, parse_date, False))
    fields.append(_simple(ctx, "fim", llm.end_date, parse_date, False))

    has_verba = any(c.tipo == "VERBA_EXPOSICAO" for c in llm.components)
    contra = _simple(ctx, "contrapartida", llm.counterpart, lambda v: v.strip()[:500], False)
    if contra["origin_kind"] in ("NORMALIZED",) or (contra["origin_kind"] == "MISSING" and not has_verba):
        contra["confirmation_status"] = "NOT_REQUIRED"
        if contra["origin_kind"] == "MISSING":
            contra["reason"] = None
    fields.append(contra)

    seen = set()
    for c in llm.components:
        key = "desconto" if c.tipo == "DESCONTO_PERCENTUAL" else "verba"
        if key in seen:
            continue
        seen.add(key)
        f = _simple(ctx, key, c.amount, parse_number, False, lambda v: (v > 0, "Valor deve ser positivo"))
        if f["origin_kind"] == "CONFLICT":
            f["raw_value"] = " · ".join(
                f"{ctx.location(ctx.register(a.evidence, key)[1]) or 'fonte'}: "
                + (pct_txt(parse_number(a.value)) if key == "desconto" else brl(parse_number(a.value)))
                for a in c.amount.alternatives) or f["raw_value"]
            f["reason"] = f.get("reason") or "Fontes divergentes"
        if key == "verba" and c.valor_basis == "POR_LOJA" and f["value"] is not None:
            stores = f_lojas["value"] or []
            n = active_stores if stores == ["REDE"] else len(stores)
            if n:
                f["value"] = round(f["value"] * n, 2)
                f["origin_kind"], f["confirmation_status"] = "NORMALIZED", "PENDING"
                f["reason"] = f"Valor por loja × {n} lojas = total da negociação (a alçada considera o total)"
        fields.append(f)

    flags: dict[str, Any] = {}
    for rx in _INJECTION:  # determinístico primeiro; o modelo complementa
        m = rx.search(ctx.body)
        if m:
            flags["injection"] = m.group(0)
            break
    if "injection" not in flags:
        for q in llm.embedded_instructions:
            span, _ = ctx.locate(q)
            if span:
                flags["injection"] = span
                break
    m = _CLAIM.search(ctx.body)  # determinístico primeiro: não depende do modelo
    if m:
        flags["approval_claim"] = m.group(0).split(":")[-1].strip()
    else:
        for q in llm.claimed_approvals:
            span, _ = ctx.locate(q)
            if span:
                flags["approval_claim"] = span
                break
    if llm.seasonal_campaign:
        span, _ = ctx.locate(llm.seasonal_campaign)
        flags["seasonal"] = span or llm.seasonal_campaign.quote
    else:
        m = _CAMPAIGN.search(ctx.body) or _CAMPAIGN.search(ctx.texts["assunto"])
        if m:
            flags["seasonal"] = m.group(0).strip()
        elif re.search(r"sazonal", ctx.body, re.I):
            flags["seasonal"] = "sazonal"
    if _THREAD.search(ctx.body):
        flags["quoted_thread"] = True
    if flags.get("injection"):
        ctx.register([LLMQuote(source="corpo", quote=flags["injection"])], None, "risk")
        for e in ctx.evidence:
            if e["text"] == flags["injection"]:
                e["kind"], e["field"] = "risk", None
    if flags.get("seasonal") and flags["seasonal"] != "sazonal" and flags["seasonal"] in ctx.body and not any(e["text"] == flags["seasonal"] for e in ctx.evidence):
        ctx.evidence.append({"text": flags["seasonal"], "field": None, "kind": "risk"})
    if not llm.is_commercial_proposal:
        flags["not_a_proposal"] = True

    # evidências não podem se sobrepor parcialmente para o destaque da fonte: mantém as mais longas
    ev, kept = sorted(ctx.evidence, key=lambda e: -len(e["text"])), []
    for e in ev:
        if not any(e["text"] in k["text"] for k in kept):
            kept.append(e)
    return {"fields": fields, "evidence": kept, "csv_highlights": ctx.csv_hl, "flags": flags, "summary_ai": llm.summary,
            "ambiguities": list(llm.ambiguities)}
