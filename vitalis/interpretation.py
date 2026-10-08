"""Converte a saída estruturada do agente de IA no formato `fields[]` da interface (handoff §3).

Tudo aqui é determinístico: validação de códigos contra o cadastro, normalização de números e
datas, localização literal das evidências na fonte e definição do que exige verificação humana
(D06). O modelo sugere; este módulo decide o que é tratado como certo e o que é suposto.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from .engine import EDITORS, LABELS, brl, pct_txt
from .extraction import LLMExtraction, LLMField, LLMQuote, parse_date, parse_number
from .policy import CATEGORIES

_LOC = {"corpo": "Corpo do e-mail", "assunto": "Assunto", "remetente": "Cabeçalho From", "anexo": "Anexo"}
_INJECTION = [re.compile(r"\[[^\]]*(processamento|sistema|automat)[^\]]*\]", re.I | re.S),
              re.compile(r"(desconsidere|ignore|ignorar)[^.]{0,80}(regras|valida)[^.]*\.", re.I | re.S)]
_CLAIM = re.compile(r"[^.\n\[]*(pr[eé]-?aprovad|j[aá] (foi )?aprovad)[^.\n]*", re.I)
_CAMPAIGN = re.compile(r"campanha [^\n.,]*", re.I)
_DIRECTIVE = re.compile(r"sistema|automat|processamento|\bIA\b|intelig[eê]ncia|rob[oô]|\bbot\b|agente|ignor|desconsider|regras?|valida|"
                        r"aprovad|aprove|status|registre|cadastre|sem (necessidade de )?revis", re.I)
_SEASON_WORDS = re.compile(r"campanha|sazona|natal|fim de ano|black ?friday|dia d[aoe]s? |p[aá]scoa|inverno|ver[aã]o|volta [aà]s aulas|"
                           r"promo[cç]", re.I)
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


_STOP = {"de", "da", "do", "das", "dos", "e", "a", "o", "na", "no", "em", "loja", "lojas", "unidade", "unidades", "drog", "vitalis"}


def _tokens(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", _norm(text)) if len(w) > 2 and w not in _STOP}


def match_stores(mention: str, lojas: list[dict]) -> list[str]:
    """Lojas cujo nome (sem o prefixo da rede) aparece inteiro na menção: 'Tijuca' → LOJA-104,
    'Barra (shopping)' → LOJA-109, 'lojas do Leblon e da Gávea' → LOJA-103 + LOJA-132.
    Nome com várias palavras exige todas ('Centro' sozinho não identifica loja)."""
    words = _tokens(mention)
    out = []
    for l in lojas:
        key = _tokens(re.sub(r"^DROG\s+(VITALIS\s+)?", "", l["nome"]))
        if key and key <= words:
            out.append(l["cod_loja"])
    return out


_GENERIC = {"ltda", "industria", "farmaceutica", "distribuidora", "produtos", "higiene", "laboratorios", "laboratorio", "farma",
            "pharma", "cosmeticos", "dermocosmeticos", "suplementos", "alimentares", "genericos", "comercio", "importacao"}


def match_supplier(texts: list[str], fornecedores: list[dict]) -> str | None:
    """Fornecedor identificado por uma palavra exclusiva da razão social (ex.: 'beta', 'nutrivida') presente
    no nome citado ou no domínio do remetente. Só devolve quando exatamente um fornecedor corresponde."""
    owners: dict[str, set[str]] = {}
    for f in fornecedores:
        for w in _tokens(f["razao_social"]) - _GENERIC:
            owners.setdefault(w, set()).add(f["cod_fornecedor"])
    unique = {w: next(iter(c)) for w, c in owners.items() if len(c) == 1 and len(w) >= 4}
    hay = " ".join(_norm(x) for x in texts if x)
    words = set(re.split(r"[^a-z0-9]+", hay))
    hits = {code for w, code in unique.items() if w in words or re.search(r"@[a-z0-9.-]*" + re.escape(w), hay)}
    return next(iter(hits)) if len(hits) == 1 else None


_PCT = re.compile(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*%")
_COMP_HINT = {"VERBA_EXPOSICAO": re.compile(r"verba|R\$|reais|valor", re.I), "DESCONTO_PERCENTUAL": re.compile(r"%|desconto|por ?cento", re.I)}


def _field(key: str, value: Any, raw: str | None, location: str, origin: str, status: str, reason: str | None) -> dict:
    return {"key": key, "label": LABELS[key], "editor": EDITORS[key], "value": value, "raw_value": raw or "não informado",
            "source_location": location, "origin_kind": origin, "confirmation_status": status, "reason": reason}


_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\d/])")
_MONEY = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)")


def _check_number(pattern):
    """O número proposto pelo modelo precisa aparecer no trecho que ele citou (ex.: '41%' → 41, não 0,41)."""
    def run(value, origin, reason, spans):
        cands = {parse_number(m) for sp in spans for m in pattern.findall(sp)}
        cands.discard(None)
        if not cands or value in cands:
            return value, origin, reason
        if len(cands) == 1:
            fixed = next(iter(cands))
            return fixed, "NORMALIZED", f"Valor ajustado ao trecho citado (o modelo havia proposto {str(value).replace('.', ',')})"
        return None, "MISSING", f"O valor proposto ({str(value).replace('.', ',')}) não aparece no trecho citado"
    return run


def _check_date(value, origin, reason, spans):
    """Datas citadas precisam existir no calendário; o modelo não pode 'corrigir' 31/02 em silêncio."""
    year = int(value[:4])
    valid, invalid = set(), []
    for sp in spans:
        for d, m, y in _DATE.findall(sp):
            yy = int(y) + (2000 if y and len(y) == 2 else 0) if y else year
            try:
                valid.add(date(yy, int(m), int(d)).isoformat())
            except ValueError:
                invalid.append(f"{d}/{m}" + (f"/{y}" if y else ""))
    if value in valid or (not valid and not invalid):
        return value, origin, reason
    if invalid:
        return None, "MISSING", f"data inexistente no calendário na fonte: {', '.join(invalid)}"
    return value, "INFERRED", "A data proposta não coincide com as datas do trecho citado; confira"


_CROSS = {"desconto": _check_number(_PCT), "verba": _check_number(_MONEY), "inicio": _check_date, "fim": _check_date}


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
    if value is not None and key in _CROSS and spans:
        value, origin, reason = _CROSS[key](value, origin, reason, spans)
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
    if forn["value"] is None and forn["origin_kind"] != "CONFLICT":
        # O modelo não sugeriu código válido: tenta a palavra exclusiva da razão social no nome citado e no remetente.
        guess = match_supplier([llm.supplier_name.value or "", ctx.texts["remetente"]], erp["fornecedores"]["itens"])
        if guess:
            forn.update(value=guess, origin_kind="INFERRED", confirmation_status="PENDING",
                        reason="Correspondência pelo nome/domínio do remetente no cadastro; confirme o fornecedor")
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
    store_rows = erp["lojas"]["itens"]
    for s in llm.stores:
        spans, src = ctx.register(s.evidence, "lojas")
        sources |= src
        mentions.append(s.mention)
        # O código sugerido pelo modelo só é aceito se existir; o nome citado na fonte prevalece quando identifica a loja.
        by_name = match_stores(s.mention, store_rows) or [c for sp in spans for c in match_stores(sp, store_rows)]
        code = (s.cod_loja or "").strip().upper() or None
        if by_name:
            codes.extend(by_name)
        elif code and code in lojas:
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

    f_ini = _simple(ctx, "inicio", llm.start_date, parse_date, False)
    f_fim = _simple(ctx, "fim", llm.end_date, parse_date, False)
    # Data inexistente escrita na fonte (ex.: 31/02/2027) explica a data ausente, mesmo que o modelo não a tenha citado.
    bad = []
    for d, m, y in _DATE.findall(ctx.body):
        try:
            date(int(y) + (2000 if len(y) == 2 else 0) if y else 2026, int(m), int(d))
        except ValueError:
            bad.append(f"{d}/{m}" + (f"/{y}" if y else ""))
    for f in (f_ini, f_fim):
        if bad and f["value"] is None and "inexistente" not in (f.get("reason") or ""):
            f["reason"] = "data inexistente no calendário na fonte: " + ", ".join(bad)
            f["confirmation_status"] = "PENDING"
    fields += [f_ini, f_fim]

    has_verba = any(c.tipo == "VERBA_EXPOSICAO" for c in llm.components)
    contra = _simple(ctx, "contrapartida", llm.counterpart, lambda v: v.strip(), False)  # sem cortar: RB09 aponta excesso
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
        if c.amount.value is None and c.amount.kind != "CONFLICT":
            # Sem valor: só é componente se a fonte de fato o menciona (ex.: "verba a definir").
            # O modelo às vezes lista "verba: ausente" em e-mails só de desconto — isso não é proposta de verba.
            cited = [ctx.locate(q)[0] for q in c.amount.evidence]
            if not any(sp and _COMP_HINT[c.tipo].search(sp) for sp in cited):
                continue
        seen.add(key)
        f = _simple(ctx, key, c.amount, parse_number, False, lambda v: (v > 0, "Valor deve ser positivo"))
        if f["origin_kind"] == "CONFLICT":
            f["raw_value"] = " · ".join(
                f"{ctx.location(ctx.register(a.evidence, key)[1]) or 'fonte'}: "
                + (pct_txt(parse_number(a.value)) if key == "desconto" else brl(parse_number(a.value)))
                for a in c.amount.alternatives) or f["raw_value"]
            f["reason"] = f.get("reason") or "Fontes divergentes"
        if key == "desconto" and f["value"] is not None and ctx.texts["anexo"] and f["origin_kind"] != "CONFLICT":
            # Verificação cruzada corpo × anexo: percentuais diferentes nas duas fontes são conflito, mesmo que o modelo
            # tenha escolhido um deles. A interpretação não escolhe entre fontes (RB16).
            body_p = {parse_number(m) for m in _PCT.findall(ctx.body)}
            att_p = {parse_number(m) for m in _PCT.findall(ctx.texts["anexo"])}
            if body_p and att_p and body_p != att_p:
                fmt = lambda s: ", ".join(pct_txt(x) for x in sorted(s))  # noqa: E731
                for cell in ctx.cells:
                    if _PCT.fullmatch(cell.strip()) and parse_number(cell) in att_p and cell not in ctx.csv_hl:
                        ctx.csv_hl.append(cell)
                f.update(value=None, origin_kind="CONFLICT", confirmation_status="PENDING",
                         raw_value=f"Corpo do e-mail: {fmt(body_p)} · {ctx.location({'anexo'})}: {fmt(att_p)}",
                         source_location=ctx.location({"corpo", "anexo"}),
                         reason="Fontes divergentes (verificação cruzada entre corpo e anexo)")
        if key == "verba" and c.valor_basis == "POR_LOJA" and f["value"] is not None:
            stores = f_lojas["value"] or []
            n = active_stores if stores == ["REDE"] else len(stores)
            if n:
                f["value"] = round(f["value"] * n, 2)
                f["origin_kind"], f["confirmation_status"] = "NORMALIZED", "PENDING"
                f["reason"] = f"Valor por loja × {n} lojas = total da negociação (a alçada considera o total)"
        fields.append(f)

    # Sinais: o agente de IA é a fonte primária. Para os dois sinais de segurança (instrução embutida e
    # alegação de aprovação), uma verificação fixa por padrão de texto funciona como rede de proteção
    # caso o modelo não os reporte. A origem de cada sinal fica registrada em `flag_sources`.
    flags: dict[str, Any] = {}
    sources_by_flag: dict[str, str] = {}

    def ai_span(quotes: list[LLMQuote]) -> str | None:
        for q in quotes:
            span, _ = ctx.locate(q)
            if span:
                return span
        return None

    def merge(key: str, ai: str | None, rule: str | None) -> None:
        if ai or rule:
            flags[key] = ai or rule
            sources_by_flag[key] = "agente+regra" if ai and rule else "agente" if ai else "regra"

    rx_inj = next((m.group(0) for rx in _INJECTION if (m := rx.search(ctx.body))), None)
    ai_inj = ai_span(llm.embedded_instructions)
    if ai_inj and not _DIRECTIVE.search(ai_inj):
        ai_inj = None  # pedido comum ao destinatário humano (ex.: "confirme assim que possível") não é instrução ao sistema
    merge("injection", ai_inj, rx_inj)
    m = _CLAIM.search(ctx.body)
    merge("approval_claim", ai_span(llm.claimed_approvals), m.group(0).split(":")[-1].strip() if m else None)
    ai_season = None
    if llm.seasonal_campaign:
        ai_season = ctx.locate(llm.seasonal_campaign)[0]
        if ai_season and not _SEASON_WORDS.search(ai_season):
            ai_season = None  # período comercial ("Q4", "trimestre") não caracteriza campanha sazonal
    m = _CAMPAIGN.search(ctx.body) or _CAMPAIGN.search(ctx.texts["assunto"])
    merge("seasonal", ai_season, m.group(0).strip() if m else ("sazonal" if re.search(r"sazonal", ctx.body, re.I) else None))
    if _THREAD.search(ctx.body):
        flags["quoted_thread"] = True
        sources_by_flag["quoted_thread"] = "regra"
    if flags.get("injection"):
        ctx.register([LLMQuote(source="corpo", quote=flags["injection"])], None, "risk")
        for e in ctx.evidence:
            if e["text"] == flags["injection"]:
                e["kind"], e["field"] = "risk", None
    if flags.get("seasonal") and flags["seasonal"] != "sazonal" and flags["seasonal"] in ctx.body and not any(e["text"] == flags["seasonal"] for e in ctx.evidence):
        ctx.evidence.append({"text": flags["seasonal"], "field": None, "kind": "risk"})
    no_terms = not any(f["key"] in ("desconto", "verba") for f in fields)
    if not llm.is_commercial_proposal or no_terms:
        # O agente declara que não é proposta, ou nenhum desconto/verba foi encontrado na fonte.
        flags["not_a_proposal"] = True
        sources_by_flag["not_a_proposal"] = ("agente+regra" if no_terms else "agente") if not llm.is_commercial_proposal else "regra"

    # evidências não podem se sobrepor parcialmente para o destaque da fonte: mantém as mais longas
    ev, kept = sorted(ctx.evidence, key=lambda e: -len(e["text"])), []
    for e in ev:
        if not any(e["text"] in k["text"] for k in kept):
            kept.append(e)
    return {"fields": fields, "evidence": kept, "csv_highlights": ctx.csv_hl, "flags": flags, "flag_sources": sources_by_flag,
            "summary_ai": llm.summary, "ambiguities": list(llm.ambiguities), "missing_fields": list(llm.missing_fields)}
