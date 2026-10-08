"""Gera public/ a partir do export do Claude Design (frontend/prototype.html).

O export é um "bundle" único (manifesto base64+gzip + template). Este script:
  1. extrai runtime, React e fontes para public/assets (sem depender de CDN em tempo de execução);
  2. remove o backend simulado no navegador e o dataset embutido (o dataset embutido tinha textos alterados);
  3. liga a interface à API real (USE_MOCK=false; exemplos lidos de /api/examples, isto é, dos .eml originais);
  4. aplica as correções de alinhamento registradas em docs/AUDITORIA_PROTOTIPO.md.

Uso:  python scripts/build_frontend.py [caminho_do_export.html]
Cada substituição é verificada: se o protótipo mudar e um trecho esperado sumir, o build falha em vez de gerar algo silenciosamente errado.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "frontend" / "prototype.html"
OUT = ROOT / "public"
ASSETS = OUT / "assets"


def section(html: str, kind: str) -> str:
    m = re.search(r'<script type="' + re.escape(kind) + r'">(.*?)</script>', html, re.S)
    if not m:
        raise SystemExit(f"Bloco {kind} não encontrado no export.")
    return m.group(1)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"[build] trecho esperado para '{label}' encontrado {n} vez(es); revise o script para o novo export.")
    return text.replace(old, new)


OVERLAY = """<sc-if value="{{ overlay }}" hint-placeholder-val="{{ false }}">
<div role="alertdialog" aria-busy="true" aria-live="polite" aria-label="{{ overlayTitle }}" style="position:fixed;inset:0;z-index:1000;background:rgba(244,243,239,.82);backdrop-filter:blur(2px);-webkit-backdrop-filter:blur(2px);display:grid;place-items:center;padding:16px;cursor:wait">
<div style="background:#fff;border:1px solid #E2E0DA;border-radius:12px;box-shadow:0 16px 48px rgba(23,25,28,.14);padding:28px 30px 24px;display:grid;justify-items:center;gap:12px;width:min(400px,100%);text-align:center">
<div style="width:42px;height:42px;border-radius:50%;border:3px solid #E3EEEB;border-top-color:#1F4D46;animation:vt-spin .9s linear infinite"></div>
<span style="font:600 16px/1.3 'IBM Plex Sans';color:#17191C">{{ overlayTitle }}</span>
<span style="font:400 13px/1.55 'IBM Plex Sans';color:#5F6368;text-wrap:pretty">{{ overlayText }}</span>
<span style="font:500 12px 'IBM Plex Mono';color:#3F4347;padding:3px 10px;border-radius:999px;background:#F4F3EF">{{ overlayElapsed }}</span>
</div>
</div>
</sc-if>
"""


AGENT_CARD = ""
AGENT_CARD_BODY = """<sc-if value="{{ hasAgent }}" hint-placeholder-val="{{ false }}">
<div style="background:#fff;border:1px solid #E2E0DA;border-radius:10px;overflow:hidden">
<div style="padding:14px 18px;border-bottom:1px solid #E2E0DA;display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:baseline"><h2 style="margin:0;font:600 14px 'IBM Plex Sans'">Leitura do agente de IA</h2><span style="font:500 11.5px 'IBM Plex Mono';color:#5F6368">{{ agentMeta }}</span></div>
<div style="padding:12px 18px;display:grid;gap:10px">
<span style="font:400 13.5px/1.55 'IBM Plex Sans';color:#17191C;text-wrap:pretty">{{ agentSummary }}</span>
<sc-if value="{{ hasAmb }}"><div style="display:grid;gap:4px"><span style="font:600 11px 'IBM Plex Mono';letter-spacing:.06em;color:#8A4B00">AMBIGUIDADES APONTADAS</span><sc-for list="{{ ambList }}" as="am"><span style="font:400 12.5px/1.5 'IBM Plex Sans';color:#3F4347">• {{ am.t }}</span></sc-for></div></sc-if>
<sc-if value="{{ hasMiss }}"><span style="font:400 12.5px/1.5 'IBM Plex Sans';color:#3F4347"><span style="font:600 11px 'IBM Plex Mono';letter-spacing:.06em;color:#B42318">NÃO ENCONTRADO NA FONTE</span> · {{ missText }}</span></sc-if>
<span style="font:400 11.5px/1.45 'IBM Plex Sans';color:#5F6368">Texto gerado pelo modelo a partir desta mensagem. Os dados abaixo foram conferidos contra a fonte; o que não tem evidência literal fica marcado para verificação.</span>
</div>
</div>
</sc-if>
"""


def apply_ux(t: str) -> str:
    # Nome do produto: Vitalis (logo, título)
    t = replace_once(t, "font:700 14px 'IBM Plex Sans'\">CC</div>", "font:700 17px 'IBM Plex Sans'\">V</div>", "logo")
    t = replace_once(t, "<span style=\"font:700 16px/1.1 'IBM Plex Sans'\">Central de Condições Comerciais</span>",
                     "<span style=\"font:700 17px/1.1 'IBM Plex Sans'\">Vitalis</span>", "nome")
    t = replace_once(t, ">Cadastro assistido de condições com fornecedores</span>",
                     ">Central de condições comerciais · cadastro assistido</span>", "subtítulo")

    # Modo técnico → rótulo e explicação claros
    t = replace_once(t, 'title="Mostra códigos de regra, controles de teste e o estado bruto da sessão. Referência de desenvolvimento."',
                     'title="Opcional, para avaliação técnica. Exibe os códigos das regras aplicadas, o modelo e o tempo da leitura por IA, '
                     'chaves de idempotência e tentativas de envio ao ERP, a simulação de ERP indisponível e o estado bruto da sessão. '
                     'Não altera o fluxo nem as decisões."', "tech: dica")
    t = replace_once(t, 'style="width:14px;height:14px;accent-color:#5F6368">Modo técnico</label>',
                     'style="width:14px;height:14px;accent-color:#5F6368">Detalhes técnicos</label>', "tech: rótulo")

    # Tela bloqueada com indicador enquanto a IA lê (e em outras operações demoradas)
    t = replace_once(t, "button:disabled{cursor:not-allowed}",
                     "button:disabled{cursor:not-allowed}\n@keyframes vt-spin{to{transform:rotate(360deg)}}\n"
                     "@keyframes vt-in{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}", "keyframes")
    t = replace_once(t, '<div style="min-height:100vh;display:flex;flex-direction:column">',
                     '<div style="min-height:100vh;display:flex;flex-direction:column">\n' + OVERLAY, "overlay")
    t = replace_once(t, "onDismissError: () => this.setState({ error: null }), hasNotice: !!s.notice, notice: s.notice,",
                     "onDismissError: () => this.setState({ error: null }), hasNotice: !!s.notice, notice: s.notice, ...this.overlayVals(), "
                     "errorDetail: err.detail || '', hasErrorDetail: tech && !!err.detail,", "overlay vals")
    t = replace_once(t, "  async call(kind, method, path, body, after) {\n    this.setState({ busy: kind, error: null, notice: null });\n"
                        "    try { const v = await this.api(method, path, body); this.setState({ v, busy: null, notice: v.notice || null }); if (after) after(v); return v; }\n"
                        "    catch (e) { this.setState({ busy: null, error: this.envelope(e) }); return null; }\n  }",
                     "  async call(kind, method, path, body, after) {\n"
                     "    this.setState({ busy: kind, busyAt: Date.now(), error: null, notice: null });\n"
                     "    clearInterval(this._tick); this._tick = setInterval(() => this.setState({ tick: Date.now() }), 250);\n"
                     "    try { const v = await this.api(method, path, body); clearInterval(this._tick); this.setState({ v, busy: null, notice: v.notice || null }); if (after) after(v); return v; }\n"
                     "    catch (e) { clearInterval(this._tick); this.setState({ busy: null, error: this.envelope(e) }); return null; }\n  }\n"
                     "  overlayVals() {\n"
                     "    const s = this.state, ms = s.busy ? Date.now() - (s.busyAt || Date.now()) : 0, secs = Math.floor(ms / 1000);\n"
                     "    const T = { analyze: ['Lendo o e-mail com IA', 'O agente está extraindo fornecedor, condições, lojas e prazos da mensagem e do anexo. Costuma levar de 15 a 60 segundos. Nada é gravado sem a sua revisão.'],\n"
                     "      open: ['Abrindo a análise', 'Carregando a análise salva desta mensagem.'],\n"
                     "      register: ['Registrando no ERP', 'Enviando a condição aprovada ao ERP simulado. Se ele estiver em manutenção, o envio é repetido automaticamente com a mesma chave.'] };\n"
                     "    const k = T[s.busy] ? s.busy : 'other', show = !!s.busy && (k !== 'other' ? ms >= 200 : ms >= 700);\n"
                     "    const tx = T[k] || ['Processando', 'Aguarde a resposta do servidor.'];\n"
                     "    return { overlay: show, overlayTitle: tx[0], overlayText: secs >= 75 && k === 'analyze' ? 'Está demorando mais que o normal — a tela continua bloqueada até a IA responder ou o servidor encerrar a tentativa.' : tx[1],\n"
                     "      overlayElapsed: secs + ' s' };\n  }", "call/overlay")
    t = replace_once(t, "    this.call('analyze', 'POST', '/api/analyze', { item_id: id }, ",
                     "    const it = ((this.state.v && this.state.v.inbox) || []).find(x => x.id === id);\n"
                     "    this.call(it && it.state ? 'open' : 'analyze', 'POST', '/api/analyze', { item_id: id }, ", "openItem kind")
    # Respostas que não são JSON (ex.: limite de tempo da hospedagem) e falha de rede viram mensagem compreensível
    t = replace_once(t, "    const r = await fetch(path, { method, credentials: 'include', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });\n"
                        "    const j = await r.json().catch(() => ({}));\n    if (!r.ok) throw Object.assign({ status: r.status }, j);",
                     "    let r;\n    try { r = await fetch(path, { method, credentials: 'include', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined }); }\n"
                     "    catch (e) { throw { code: 'NETWORK', message: 'Sem conexão com o servidor. Verifique a internet e tente de novo.' }; }\n"
                     "    const j = await r.json().catch(() => null);\n"
                     "    if (!r.ok) throw Object.assign({ status: r.status }, j && j.code ? j : { code: 'HTTP_' + r.status, message: r.status === 504 ? 'O servidor excedeu o tempo limite da hospedagem antes de concluir. Nada foi gravado; tente de novo.' : 'O servidor respondeu com erro ' + r.status + ' sem detalhes. Nada foi gravado; tente de novo.' });\n"
                     "    if (!j) throw { code: 'INVALID_RESPONSE', message: 'Resposta inesperada do servidor.' };", "api errors")
    t = replace_once(t, "  envelope(e) { return { status: e.status || null, code: e.code || 'ERRO', message: e.message || String(e), correlation_id: e.correlation_id || null, findings: e.findings || [] }; }",
                     "  envelope(e) { return { status: e.status || null, code: e.code || 'ERRO', message: e.message || String(e), correlation_id: e.correlation_id || null, findings: e.findings || [], detail: e.detail || null }; }", "envelope detail")
    t = replace_once(t, '<sc-for list="{{ errorFindings }}" as="ef">',
                     '<sc-if value="{{ hasErrorDetail }}" hint-placeholder-val="{{ false }}"><span style="font:400 11.5px/1.45 \'IBM Plex Mono\';color:#7A271A;word-break:break-word">{{ errorDetail }}</span></sc-if>\n'
                     '<sc-for list="{{ errorFindings }}" as="ef">', "error detail")

    # Caixa de entrada primeiro; simulação de e-mail recolhida atrás de um botão
    t = replace_once(t, "    draft: { changes: {}, confirms: {}, acks: null }, editKey: null,",
                     "    simOpen: false, busyAt: 0, tick: 0,\n    draft: { changes: {}, confirms: {}, acks: null }, editKey: null,", "state simOpen")
    t = replace_once(t, '<span style="font:500 11.5px \'IBM Plex Sans\';color:#5F6368">Mensagens recebidas nesta sessão</span>',
                     '<sc-if value="{{ simClosed }}" hint-placeholder-val="{{ true }}"><button sc-camel-on-click="{{ onOpenSim }}" style="border:1px solid #1F4D46;background:#fff;color:#1F4D46;font:600 13.5px \'IBM Plex Sans\';padding:9px 16px;border-radius:7px;cursor:pointer;display:flex;gap:8px;align-items:center" style-hover="background:#E3EEEB"><span style="font:600 16px/1 \'IBM Plex Sans\'">+</span>Simular novo e-mail</button></sc-if>\n'
                     '<sc-if value="{{ simOpen }}" hint-placeholder-val="{{ false }}"><span style="font:500 11.5px \'IBM Plex Sans\';color:#5F6368">Mensagens recebidas nesta sessão</span></sc-if>', "botão simular")
    t = replace_once(t, '<div style="flex:1 1 520px;min-width:0;background:#fff;border:1px solid #E2E0DA;border-radius:10px">',
                     '<sc-if value="{{ simOpen }}" hint-placeholder-val="{{ false }}">\n'
                     '<div id="sim-panel" style="flex:1 1 520px;min-width:0;background:#fff;border:1px solid #E2E0DA;border-radius:10px;animation:vt-in .22s ease-out">', "painel simular abre")
    t = replace_once(t, "{{ receiveLabel }}</button>\n</div>\n</div>\n</section>",
                     "{{ receiveLabel }}</button>\n</div>\n</div>\n</sc-if>\n</section>", "painel simular fecha")
    t = replace_once(t, '<span style="font:500 11.5px \'IBM Plex Mono\';padding:4px 9px;border-radius:999px;color:{{ srcFg }};background:{{ srcBg }}">{{ srcLabel }}</span>\n</div>',
                     '<div style="display:flex;gap:10px;align-items:center"><span style="font:500 11.5px \'IBM Plex Mono\';padding:4px 9px;border-radius:999px;color:{{ srcFg }};background:{{ srcBg }}">{{ srcLabel }}</span>'
                     '<button sc-camel-on-click="{{ onCloseSim }}" title="Recolher" aria-label="Recolher simulação" style="width:30px;height:30px;border:1px solid #D6D3CB;background:#fff;border-radius:7px;color:#3F4347;font:500 17px/1 \'IBM Plex Sans\';cursor:pointer" style-hover="background:#F4F3EF">×</button></div>\n</div>', "fechar simular")
    t = replace_once(t, "Simule o recebimento de um e-mail ao lado — escrito livremente ou a partir de um exemplo do dataset.",
                     "Use “Simular novo e-mail” para criar uma mensagem — escrita livremente ou a partir de um exemplo do dataset.", "texto vazio")
    t = replace_once(t, "      onReceive: () => this.receive(),",
                     "      simOpen: !!s.simOpen, simClosed: !s.simOpen, onOpenSim: () => { this.setState({ simOpen: true }); this.scrollToId('sim-panel'); }, onCloseSim: () => this.setState({ simOpen: false }),\n"
                     "      onReceive: () => this.receive(),", "vals simular")
    t = replace_once(t, "    this.call('receive', 'POST', '/api/inbox', body, () => this.setState({ form: { from: '', subject: '', body: '', csvName: '', csv: '' }, exampleId: null, edited: false, notice:",
                     "    this.call('receive', 'POST', '/api/inbox', body, () => this.setState({ simOpen: false, form: { from: '', subject: '', body: '', csvName: '', csv: '' }, exampleId: null, edited: false, notice:", "receive fecha")
    t = replace_once(t, "  reset() { this.call('reset', 'POST', '/api/reset', {}, () => this.setState({ step: 1,",
                     "  reset() { this.call('reset', 'POST', '/api/reset', {}, () => this.setState({ simOpen: false, step: 1,", "reset fecha")
    # Leitura do agente de IA: resumo, ambiguidades e dados não encontrados vêm direto da saída do modelo
    t = replace_once(t, "<div style=\"min-width:0;display:grid;gap:16px\">\n<sc-if value=\"{{ hasInsights }}\" hint-placeholder-val=\"{{ false }}\">",
                     AGENT_CARD + "<div style=\"min-width:0;display:grid;gap:16px\">\n" + AGENT_CARD_BODY + "<sc-if value=\"{{ hasInsights }}\" hint-placeholder-val=\"{{ false }}\">", "card agente")
    t = replace_once(t, "    const fl = p.flags || {}, ins = [];\n"
                        "    if (fl.injection) ins.push(['RISCO', 'Instrução dirigida a processamento automático', 'Tratada como dado não confiável: não altera regras, aprovação nem cadastro.', fl.injection]);\n"
                        "    if (fl.approval_claim) ins.push(['RISCO', 'Alegação de aprovação prévia', 'Afirmação do fornecedor não é evidência de aprovação da empresa.', fl.approval_claim]);\n"
                        "    if (fl.seasonal) ins.push(['CONTEXTO', 'Menção a campanha sazonal', 'Pode envolver a exceção de teto da política; a validação de regras avalia.', fl.seasonal === 'sazonal' ? '' : fl.seasonal]);\n"
                        "    if (fl.quoted_thread) ins.push(['CONTEXTO', 'Mensagem com histórico citado', 'Confira se os valores vêm da mensagem atual e não do histórico.', '']);\n",
                     "    const fl = p.flags || {}, fs = p.flag_sources || {}, ins = [];\n"
                     "    if (fl.not_a_proposal) ins.push(['RISCO', 'Sem proposta comercial identificada', 'Nenhum desconto ou verba foi encontrado na mensagem. Confira antes de seguir.', '', 'not_a_proposal']);\n"
                     "    if (fl.injection) ins.push(['RISCO', 'Instrução dirigida a processamento automático', 'Tratada como dado não confiável: não altera regras, aprovação nem cadastro.', fl.injection, 'injection']);\n"
                     "    if (fl.approval_claim) ins.push(['RISCO', 'Alegação de aprovação prévia', 'Afirmação do fornecedor não é evidência de aprovação da empresa.', fl.approval_claim, 'approval_claim']);\n"
                     "    if (fl.seasonal) ins.push(['CONTEXTO', 'Menção a campanha sazonal', 'Pode envolver a exceção de teto da política; a validação de regras avalia.', fl.seasonal === 'sazonal' ? '' : fl.seasonal, 'seasonal']);\n"
                     "    if (fl.quoted_thread) ins.push(['CONTEXTO', 'Mensagem com histórico citado', 'Confira se os valores vêm da mensagem atual e não do histórico.', '', 'quoted_thread']);\n"
                     "    const SRCL = { agente: 'Detectado pelo agente de IA', regra: 'Detectado pela verificação fixa do texto (sem IA)', 'agente+regra': 'Detectado pelo agente de IA e pela verificação fixa' };\n"
                     "    const agentMeta = (p.extractor || '').replace(/^Agente de IA · /, ''), amb = p.ambiguities || [], miss = p.ai_missing_fields || [];\n", "insights origem")
    t = replace_once(t, "      insights: ins.map(a => ({ tag: a[0], tFg: a[0] === 'RISCO' ? '#B42318' : '#3D5A73', tBg: a[0] === 'RISCO' ? '#FDECEA' : '#E8EEF3', title: a[1], text: a[2], quote: a[3], hasQuote: !!a[3] })), hasInsights: ins.length > 0, aiSummary,",
                     "      insights: ins.map(a => ({ tag: a[0], tFg: a[0] === 'RISCO' ? '#B42318' : '#3D5A73', tBg: a[0] === 'RISCO' ? '#FDECEA' : '#E8EEF3', title: a[1], text: a[2], quote: a[3], hasQuote: !!a[3], srcLabel: SRCL[fs[a[4]]] || 'Detectado pelo agente de IA' })), hasInsights: ins.length > 0, aiSummary,\n"
                     "      hasAgent: !!p.ai_summary, agentSummary: p.ai_summary || '', agentMeta, hasAmb: amb.length > 0, ambList: amb.map(x => ({ t: x })), hasMiss: miss.length > 0, missText: miss.join(', '),", "vals agente")
    t = replace_once(t, '<span style="font:400 12.5px/1.5 \'IBM Plex Sans\';color:#3F4347;text-wrap:pretty">{{ in.text }}</span>',
                     '<span style="font:400 12.5px/1.5 \'IBM Plex Sans\';color:#3F4347;text-wrap:pretty">{{ in.text }}</span>\n'
                     '<span style="font:500 11px \'IBM Plex Sans\';color:#5F6368">{{ in.srcLabel }}</span>', "insight origem rótulo")
    t = replace_once(t, "Leitura da IA sobre a mensagem. Não exige ação aqui; os efeitos aparecem na validação de regras.",
                     "Sinais encontrados na mensagem. Não exigem ação aqui; os efeitos aparecem na validação de regras.", "insights subtítulo")
    # dica dos exemplos: sem resultado esperado pré-escrito
    t = replace_once(t, "title: e.letter + ' · ' + e.who + ' — ' + e.desc + ' Esperado: ' + e.expect + '.',",
                     "title: e.letter + ' · ' + e.who + ' — e-mail original do dataset',", "dica exemplos")
    t = replace_once(t, "  componentWillUnmount() {", "  componentWillUnmount() { clearInterval(this._tick);", "unmount") if "  componentWillUnmount() {" in t else t
    # Cabeçalho: marca à esquerda, 4 etapas centralizadas na tela, reiniciar à direita; selos e detalhes técnicos numa faixa abaixo
    m = re.search(r'<header style="[^"]*">(.*?)</header>', t, re.S)
    inner = m.group(1)
    logo = re.search(r'(<div style="display:flex;align-items:center;gap:12px">.*?</div>\n</div>)\n<nav', inner, re.S).group(1)
    nav = re.search(r'<nav style="[^"]*">(.*?)</nav>', inner, re.S).group(1)
    chips = re.search(r'(<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center" title="\{\{ backendLabel \}\}.*?</div>)', inner, re.S).group(1)
    tech = re.search(r'(<label title="Opcional.*?</label>)', inner, re.S).group(1)
    reset = re.search(r'(<button sc-camel-on-click="\{\{ onReset \}\}".*?</button>)', inner, re.S).group(1)
    reset = reset.replace('padding:8px 14px', 'padding:8px 14px;white-space:nowrap')
    reset = reset.replace('>Reiniciar demonstração</button>', '>{{ resetLabel }}</button>')
    chips = chips.replace("font:600 11.5px 'IBM Plex Mono';padding:5px 9px", "font:600 11px 'IBM Plex Mono';padding:3px 8px").replace(
        "font:500 11.5px 'IBM Plex Sans';padding:5px 9px", "font:500 11px 'IBM Plex Sans';padding:3px 8px")
    header = ('<header style="background:#fff;border-bottom:1px solid #E2E0DA">\n'
              '<div style="display:grid;grid-template-columns:{{ hdCols }};align-items:center;gap:12px 20px;padding:12px 24px 8px">\n'
              '<div style="grid-column:1;grid-row:1;justify-self:start;min-width:0">' + logo + '</div>\n'
              '<nav aria-label="Etapas" style="grid-column:{{ navCol }};grid-row:{{ navRow }};justify-self:{{ navJustify }};display:flex;align-items:center;gap:6px;flex-wrap:{{ navWrap }}">' + nav + '</nav>\n'
              '<div style="grid-column:{{ resetCol }};grid-row:1;justify-self:end">' + reset + '</div>\n'
              '</div>\n'
              '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px 16px;flex-wrap:wrap;padding:0 24px 10px">' + chips + tech + '</div>\n'
              '</header>')
    t = t[:m.start()] + header + t[m.end():]
    t = replace_once(t, "      onDismissError: () => this.setState({ error: null }),",
                     "      hdCols: s.hdWide ? 'minmax(0,1fr) auto minmax(0,1fr)' : 'minmax(0,1fr) auto', navCol: s.hdWide ? '2' : '1 / -1', navRow: s.hdWide ? '1' : '2',\n"
                     "      navJustify: s.hdWide ? 'center' : 'start', navWrap: s.hdWide ? 'nowrap' : 'wrap', resetCol: s.hdWide ? '3' : '2', resetLabel: s.wide ? 'Reiniciar demonstração' : 'Reiniciar',\n"
                     "      onDismissError: () => this.setState({ error: null }),", "header vals")
    t = replace_once(t, "    this._rs = () => { const w = window.innerWidth >= 920; if (w !== this.state.wide) this.setState({ wide: w }); }; window.addEventListener('resize', this._rs);",
                     "    this._rs = () => { const w = window.innerWidth >= 920, h = window.innerWidth >= 1180; if (w !== this.state.wide || h !== this.state.hdWide) this.setState({ wide: w, hdWide: h }); }; window.addEventListener('resize', this._rs); this._rs();",
                     "header resize")
    t = replace_once(t, "    simOpen: false, busyAt: 0, tick: 0,", "    simOpen: false, busyAt: 0, tick: 0, hdWide: typeof window !== 'undefined' ? window.innerWidth >= 1180 : true,", "state hdWide")

    # Escopo: link discreto que abre um painel
    t = replace_once(t, '<details style="background:#fff;border:1px solid #E2E0DA;border-radius:10px">\n'
                        '<summary style="padding:14px 20px;font:600 13.5px \'IBM Plex Sans\';cursor:pointer">Sobre esta demonstração · o que está dentro e fora do escopo</summary>\n'
                        '<div style="padding:4px 20px 18px;display:grid;',
                     '<details style="justify-self:start;max-width:100%">\n'
                     '<summary style="font:500 12.5px \'IBM Plex Sans\';color:#5F6368;cursor:pointer;width:fit-content">Sobre esta demonstração e o escopo</summary>\n'
                     '<div style="margin-top:8px;background:#fff;border:1px solid #E2E0DA;border-radius:10px;padding:14px 20px 16px;display:grid;', "escopo discreto")

    # Prévia das regras ao lado de cada dado na etapa 2 (a avaliação completa continua na etapa 3)
    t = replace_once(t, "      const notes = [f.reason, f.corrected_from ? 'Valor anterior: ' + f.corrected_from : null].filter(Boolean);",
                     "      const notes = [f.reason, f.corrected_from ? 'Valor anterior: ' + f.corrected_from : null].filter(Boolean);\n"
                     "      const pol = changed ? [] : (p.findings || []).filter(x => (x.affected_fields || []).includes(f.key) && x.rule_id !== 'RB16' && (x.severity === 'BLOCKER' || x.severity === 'WARNING'))\n"
                     "        .map(x => ({ t: (x.severity === 'BLOCKER' ? 'Política · impede o cadastro: ' : 'Política · aviso: ') + x.message.replace(/\\.\\s[\\s\\S]*$/, '.'), fg: x.severity === 'BLOCKER' ? '#B42318' : '#8A4B00' }));", "prévia regras")
    t = replace_once(t, "numHint: f.editor === 'percent' ? 'Ex.: 12,5' : 'Ex.: 8000,00', hasNote: notes.length > 0, note: notes.join(' · ') };",
                     "numHint: f.editor === 'percent' ? 'Ex.: 12,5' : 'Ex.: 8000,00', hasNote: notes.length > 0, note: notes.join(' · '), policy: pol, hasPolicy: pol.length > 0 };", "vals prévia")
    t = replace_once(t, '<sc-if value="{{ f.hasNote }}"><span style="font:400 12px/1.45 \'IBM Plex Sans\';color:#3F4347">{{ f.note }}</span></sc-if>',
                     '<sc-if value="{{ f.hasNote }}"><span style="font:400 12px/1.45 \'IBM Plex Sans\';color:#3F4347">{{ f.note }}</span></sc-if>\n'
                     '<sc-if value="{{ f.hasPolicy }}"><sc-for list="{{ f.policy }}" as="pl"><span style="font:500 12px/1.45 \'IBM Plex Sans\';color:{{ pl.fg }}">{{ pl.t }}</span></sc-for></sc-if>', "template prévia")
    return t


def main() -> None:
    html = SRC.read_text(encoding="utf-8")
    manifest = json.loads(section(html, "__bundler/manifest"))
    template = json.loads(section(html, "__bundler/template"))
    ext = {e["uuid"]: e["id"] for e in json.loads(section(html, "__bundler/ext_resources"))}

    if OUT.exists():
        shutil.rmtree(OUT)
    (ASSETS / "fonts").mkdir(parents=True)

    names: dict[str, str] = {}
    runtime_uuid = None
    for uid, meta in manifest.items():
        raw = base64.b64decode(meta["data"])
        if meta.get("compressed"):
            raw = gzip.decompress(raw)
        mime = meta["mime"]
        if mime.startswith("font/"):
            rel = f"assets/fonts/{uid}.woff2"
        elif uid in ext:
            rel = "assets/" + ext[uid].rsplit("/", 1)[-1]  # react.production.min.js / react-dom.production.min.js
        else:
            text = raw.decode("utf-8", "replace")
            if "dc-runtime" in text[:200]:
                rel, runtime_uuid = "assets/dc-runtime.js", uid
            elif "window.CC_DATASET" in text[:400] or "backend SIMULADO" in text[:400]:
                names[uid] = ""  # descartado
                continue
            else:
                rel = f"assets/{uid}.js"
        (OUT / rel).write_bytes(raw)
        names[uid] = rel
    if not runtime_uuid:
        raise SystemExit("Runtime do Claude Design não encontrado no export.")

    t = template
    # 1) runtime local + React servido pelo próprio site (mapa de recursos lido pelo runtime)
    resources = {url: "/" + names[uid] for uid, url in ext.items()}
    t = replace_once(t, f'<script src="{runtime_uuid}"></script>',
                     "<title>Vitalis</title>\n"
                     '<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 32 32%22%3E%3Crect width=%2232%22 height=%2232%22 rx=%226%22 fill=%22%231F4D46%22/%3E%3Ctext x=%2216%22 y=%2221%22 font-family=%22monospace%22 font-size=%2216%22 font-weight=%22700%22 text-anchor=%22middle%22 fill=%22white%22%3EV%3C/text%3E%3C/svg%3E">\n'
                     '<meta name="description" content="Cadastro assistido de condições comerciais com fornecedores — demonstração com dados fictícios.">\n'
                     f"<script>window.__resources = {json.dumps(resources)};</script>\n"
                     '<script src="/assets/dc-runtime.js"></script>', "runtime")
    # 2) remove backend simulado, dataset embutido e notas de handoff
    for uid, rel in names.items():
        if rel == "":
            t = replace_once(t, f'<script src="{uid}"></script>', "", f"script descartado {uid}")
    t = re.sub(r'<script type="text/markdown" id="handoff">.*?</script>', "", t, flags=re.S)
    # fontes
    for uid, rel in names.items():
        if rel.startswith("assets/fonts/"):
            t = t.replace(f'url("{uid}")', f'url("/{rel}")')
    if re.search(r'url\("[0-9a-f-]{36}"\)', t):
        raise SystemExit("Restou referência de recurso não resolvida no template.")

    # 3) ligação com a API real
    t = replace_once(t, "static USE_MOCK = true;", "static USE_MOCK = false;", "USE_MOCK")
    t = replace_once(t, "fetch('dataset/fixtures.json')", "fetch('/api/examples', { credentials: 'include' })", "fixtures")
    t = replace_once(t, "backendLabel: C.USE_MOCK ? 'backend simulado no navegador (sem IA)' : 'backend conectado',",
                     "backendLabel: C.USE_MOCK ? 'backend simulado no navegador (sem IA)' : 'backend conectado · leitura por agente de IA',", "backendLabel")

    # 4) correções de alinhamento (docs/AUDITORIA_PROTOTIPO.md)
    t = replace_once(t, "<li>Leitura por modelo de IA nesta versão da interface: a extração é simulada por regras sobre o texto</li>",
                     "<li>Garantia de acerto da leitura por IA: todo dado incerto passa pela sua verificação antes das regras</li>",
                     "escopo: IA")
    t = replace_once(t, "<li>E-mail em texto livre, com ou sem planilha anexa, como entrada</li>",
                     "<li>E-mail em texto livre, com ou sem planilha anexa, como entrada</li>"
                     "<li>Leitura do e-mail por agente de IA, com evidência do trecho de origem de cada dado</li>",
                     "escopo: dentro")
    # 5) ajustes de usabilidade pedidos após a primeira publicação
    t = apply_ux(t)

    (OUT / "index.html").write_text(t, encoding="utf-8")
    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"public/ gerado: {len(list(OUT.rglob('*')))} itens, {total / 1024:.0f} KB")


if __name__ == "__main__":
    main()
