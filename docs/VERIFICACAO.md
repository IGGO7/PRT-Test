# Verificação — o que é IA, o que é regra e como foi provado

Data: 08/10/2026 · versão publicada verificada: commits `4541e60` e `424b15e` (87/87 nas duas rodadas finais).

## 1. De onde vem cada coisa na tela

| Elemento | Origem | Como é controlado |
|---|---|---|
| Valores em **Dados propostos** (fornecedor, categoria, lojas, datas, contrapartida, desconto, verba) | **Agente de IA** (`create_agent` + `gpt-5-mini`, chamada real a cada análise) | Código valida: código existe no cadastro, número/data normalizados, citação precisa existir literalmente na fonte. O que não passa vira *Ausente* ou *Inferido* e fica "A verificar". |
| Marcações amarelas na mensagem e células destacadas no CSV | Trechos **citados pelo agente** | Só é marcado o que foi encontrado literalmente no texto; nada é sintetizado. |
| Card **Leitura do agente de IA** (resumo, ambiguidades, dados não encontrados) | **Texto do modelo**, sem edição | Exibe o modelo e o tempo da leitura. |
| Cards de **sinais** (instrução embutida, alegação de aprovação, campanha sazonal, não-proposta) | **Agente primeiro**; verificação fixa de texto como rede de proteção | Cada card mostra a origem: agente, verificação fixa ou ambos. "Histórico citado" é só verificação fixa. |
| Apontamentos (RB01–RB16, D05, SEG), alçada, estados, conclusões automáticas | **Motor de regras determinístico** (`vitalis/engine.py`, POL-COM-004 v3.2) | Por desenho (ADR-06), a IA não decide política. Coberto por 44 testes de limites. |
| ERP, aprovadores, recebimento do e-mail | **Simulados** (declarado na tela) | ERP segue o contrato do dataset (idempotência, 422/429/503). |
| Textos fixos | Rótulos da interface e nome dos 4 exemplos | Nenhum resultado esperado é pré-escrito. Nenhum código depende do nome do fornecedor ou do cenário. |

**Reservas determinísticas para falhas do modelo** (sempre marcadas para confirmação humana):
- fornecedor pela palavra exclusiva da razão social no nome ou no domínio do remetente;
- lojas pelo nome citado;
- conflito de percentual entre corpo e anexo;
- componente sem valor e sem menção na fonte é descartado;
- sinal do modelo sem relação com o processamento (instrução) ou sem campanha (sazonal) é descartado.

## 2. Como foi verificado

1. **89 testes locais** (`pytest`), sem rede:
   - fluxo dos 4 cenários;
   - contrato do ERP;
   - validações da saída do agente;
   - **matriz de regras** (`tests/test_rules_matrix.py`, 44 casos): limites exatos de teto por categoria, faixas de alçada (10 / 10,01 / 20 / 25 / 25,01; R$ 5.000 / 5.000,01 / 20.000 / 20.000,01), vigência (retroativa, ordem, 12 meses exatos e +1 dia), contrapartida, fornecedor inativo e inexistente, sobreposição (parcial, REDE, tipo diferente), substituição D07, componentes múltiplos, exceção sazonal (6 combinações), sinais de segurança, conclusões AUTO-01/03/04 e ciência de avisos.
2. **Ponta a ponta no site publicado com o agente real** (`tests/e2e/prod_check.py`):
   - 13 cenários: os 4 do dataset, 6 variações do texto e 3 e-mails inéditos;
   - roda automaticamente a cada push (`.github/workflows/e2e-producao.yml`), espera a Vercel publicar o mesmo commit e grava o relatório na branch `e2e-report`;
   - as variações provam que o resultado acompanha o texto: 13% → 9% muda a alçada para Coordenação; 13% → 22% gera bloqueio de teto; data retroativa e vigência de 15 meses geram RB04; tirar "campanha" da Alfa troca RB13 por RB02; tirar a nota da Nutrivida remove os sinais de risco;
   - dois e-mails inéditos completos: um chega a cadastrado no ERP simulado e um fica bloqueado por exigir o Comitê.

| Rodada | Commit | Resultado | O que revelou |
|---|---|---|---|
| 1 | 82310fe | 71/78 | Verba "ausente" virando componente, o que travava Beta, Alfa e Nutrivida. Lojas sem código. Pedido comum marcado como instrução. |
| 2 | 50c936e | 77/80 | Fornecedor da Beta sem código. Conflito 12% × 12,5% da Gama não apontado. |
| 3 | c1132a5 | 84/84 | — |
| 4–5 | 261d62b, 0a8db14 | 83/84 | E-mail sem proposta marcado como proposta. "Q4" tratado como campanha. |
| 6 | f3accf0 | 85/85 | — |
| 7–8 | 4541e60, 424b15e | **87/87** | — |

## 3. Ajustes de regra feitos nesta verificação

- **RB05 período:** contrapartida de verba sem período de execução passa de aviso a **bloqueio**, conforme a Política §7 e o SSoT §23.
- **Exceção sazonal (*):** só é considerada quando a fonte invoca campanha. Sem campanha, vale o RB02 (teto). Acima de 30% vale o RB02 + vedação. A mensagem distingue acima de 25% (precedência indefinida) de 25% ou menos (critérios de aprovação indefinidos).

## 4. Limites conhecidos

- O modelo não é determinístico. Por isso todo dado incerto exige confirmação humana e as reservas acima cobrem as falhas observadas. O e2e de cada push detecta regressões. Cada rodada faz 13 chamadas ao modelo.
- A caixa de entrada nasce com os 4 e-mails do dataset por pedido do responsável em 08/10/2026. Isso ajusta a orientação anterior do SSoT (§1138, D02) de não ter fila inicial. O formulário livre continua disponível em "Simular novo e-mail".
