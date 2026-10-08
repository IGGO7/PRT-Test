######## https://edu.pareto.io/embed/case-tecnico-ai-builder.html
• 

Case Técnico: AI Builder

 Case técnico · AI Builder

 Cadastro de condições comerciais de fornecedores no ERP, da descoberta
 à entrega.

 Prazo
48 horas corridas

 Entrega
PDF, link público e vídeo

 Ferramentas
Livre escolha

 Envio
Formulário da Pareto

 O papel do AI Builder

 O trabalho começa na operação, antes de qualquer solução, e
 termina com o usuário final usando o que foi entregue. Este case reproduz uma demanda real do time.

 Entender antes de construir

 O processo de negócio vem primeiro. Quem resolve o enunciado literal costuma resolver
 o problema errado.

 Decidir onde a IA entra

 E, principalmente, onde ela não deve entrar. Nem todo passo do processo melhora com
 um modelo no meio.

 Conectar o que não conversa

 Sistema corporativo, documento solto, planilha, caixa de entrada. Fazer essas fontes
 conversarem é parte do trabalho.

 Construir a solução

 Você usa a IA para construir a ferramenta que a área de negócio vai operar.
 Ela precisa funcionar, e a pessoa precisa querer usar.

 Assumir a ambiguidade

 A regra raramente chega pronta. Assumir uma premissa de forma explícita e registrar a
 dúvida faz parte do trabalho.

 Defender a proposta

 Em algum momento você vai apresentar isso para quem não é técnico,
 sem ninguém traduzindo, e mostrar o retorno.

 Contexto

 Cenário fictício, inspirado em problemas comuns do setor: uma distribuidora
 farmacêutica que também opera uma rede própria de cerca de 40 drogarias.
 Alto volume, margem apertada. Pequenas ineficiências operacionais viram números
 grandes quando multiplicadas por centenas de fornecedores.

 O ambiente de tecnologia é o típico de uma empresa desse porte: SAP
 como ERP, Databricks como plataforma de dados, SharePoint e Teams
 para documentos e comunicação, Azure para hospedar as
 aplicações internas, e a caixa de entrada do time comercial como porta de
 entrada das negociações.

 Para este case, você não precisa de acesso a nenhum desses sistemas. O dataset
 anexo traz recortes deles e uma simulação do ERP.

 O problema

 Todo mês, o time comercial negocia centenas de condições com fornecedores:
 percentual de desconto, verba de exposição em loja, contrapartidas, prazo de
 vigência. Elas chegam por e-mail, em texto livre, às vezes com uma planilha anexa,
 às vezes só no corpo da mensagem.

 Depois de negociadas, precisam ser conferidas contra a política comercial da empresa e
 cadastradas no ERP. Hoje esse caminho é inteiramente humano: uma pessoa lê o e-mail,
 interpreta, procura a regra que se aplica, decide se está dentro da alçada e digita
 no sistema.

 O processo é lento, porque consome tempo de gente cara em leitura e
 digitação. É inconsistente, porque duas pessoas leem o mesmo
 e-mail e cadastram de formas diferentes. E é falho, porque o erro
 normalmente só aparece semanas depois, no fechamento do mês, quando já virou
 glosa ou desgaste com um fornecedor estratégico.

 É aí que entra a IA: velocidade e consistência na leitura e na
 conferência. E é aí que entra o humano no meio: previsibilidade, para que
 nada chegue ao ERP sem alguém responder por aquilo.

 Importante
 Esta descrição é deliberadamente incompleta, como qualquer problema de
 negócio costuma ser. Parte do exercício é você explicitar o que
 assumiria, o que perguntaria e a quem, antes de desenhar a solução.

 O que construir

 Uma solução de ponta a ponta. A entrada e a saída estão
 definidas; o que acontece entre as duas é a sua proposta.

 EntradaE-mail do fornecedorTexto livre, com ou sem anexo, do jeito que chega.

 —Sua soluçãoAgentes de IA, regras e interface. Como você decidir.

 SaídaCondição no ERPGravada no sistema, depois de aprovada por uma pessoa.

 Requisitos

 São resultados esperados, não instruções de como construir.

• A solução precisa usar agentes de IA. Quantos, com quais papéis, com que divisão de responsabilidade e em quais etapas — são decisões suas, e queremos ver o argumento por trás delas.

• O que chega em texto livre precisa virar dado estruturado e confiável, com alguma noção do que foi extraído com certeza e do que foi suposto.

• A condição precisa ser confrontada com a política comercial e com o que já existe no ERP antes de qualquer gravação.

• Precisa haver uma interface onde uma pessoa da área de negócio confira, corrija e decida. A usabilidade dela é avaliada: a pessoa precisa conseguir usar sem treinamento, e gostar de usar.

• A condição aprovada precisa chegar ao ERP no formato que ele aceita. O contrato do ERP está descrito no README da simulação, no dataset.

 Sobre o desenho da solução
 Não existe desenho esperado. O que avaliamos é se você sustenta a escolha
 que fez.

 Dataset

 Anexo a este case. Nada nele diz como as fontes se ligam — construir essa
 ligação é parte do exercício.

 Baixar dataset (.zip)
 https://edu.pareto.io/__l5e/assets-v1/842f08bf-d65e-4db4-9243-9302a681c55f/dataset-case-ai-builder.zip

 dataset/
├── emails/ 4 propostas de fornecedor
│ └── anexos/ planilhas citadas nos e-mails
├── extrato_erp.csv export do ERP, gerado ontem
├── Politica_Comercial.pdf regras de alçada e limite
├── mock_erp/ simulação do ERP + README do contrato
└── README.md dicionário de dados

 Os e-mails

 Escritos como fornecedores escrevem: sem padrão, com informação
 implícita e nomes comerciais que não são os do sistema.

 O extrato do ERP

 O mesmo mundo visto por outro sistema, com códigos e vocabulário próprios.
 É um export gerado no dia anterior, do jeito que a área costuma receber.

 A política comercial

 O documento oficial da área, em PDF, do jeito que ele circula internamente.

 A simulação do ERP

 Reproduz o comportamento do sistema real. O README descreve o contrato: o que o ERP aceita,
 o que recusa e o que devolve. Leia antes de definir o que a solução entrega ao ERP.

 Entrega

 Um PDF, um link público da solução e um vídeo. O PDF é
 o documento que circularia com a área de negócio; o link é a prova de que a
 solução existe e funciona.

 1 · Documento em PDF

 Arquivo único, com os links do que for externo. Dez seções, nesta ordem.

 SeçãoO que precisa ter

 01Descoberta. Quais áreas você ouviria, o que perguntaria a cada uma, quais premissas assumiu e o risco de cada premissa estar errada.

 02AS-IS. Fluxograma do processo atual como você o entendeu.

 03TO-BE. Fluxograma do processo com a sua solução no lugar.

 04Desenho da solução. Quais peças compõem a solução, quais ferramentas você usou e por quê, pensando em custo, manutenção e quem vai operar isso.

 05Os agentes. Quantos, com que papel cada um, e por que essa divisão. Prompt completo, formato de saída e onde você decidiu não usar IA.

 06A interface. Prints comentados das telas e as decisões de desenho: o que a pessoa vê, em que ordem, e por quê. Quem é o usuário e o que você fez para ele não precisar de treinamento.

 07Demonstração. Vídeo de até 3 minutos mostrando um e-mail entrando e uma condição sendo aprovada e gravada. Link no documento.

 08Decisões e trade-offs. Cada decisão relevante com contexto, opções consideradas, escolha e consequência. Inclua as perguntas que você levaria para a área de negócio e como saberia, em produção, que a solução está errando.

 09Plano e ROI. Fases de implementação, atividades-chave, esforço estimado e os indicadores que você acompanharia. Ao final: qual o retorno desse projeto?

 10Manual da solução. Escrito para o usuário de negócio, sem jargão: como usar a interface, quais regras o sistema aplica, o que ele não faz e o que fazer quando algo dá errado.

 2 · Solução publicada

 Aqui entra a comprovação da solução desenvolvida. Você pode provar
 que a desenvolveu da maneira que achar melhor, mas lembre-se: um profissional de negócio
 irá avaliar sua entrega e nós temos dezenas de cases para analisar. Pense em facilitar
 a vida da banca avaliadora.

 O único requisito é um link público: qualquer pessoa abre direto
 no navegador, sem login, sem pedido de acesso e sem instalar ou rodar nada localmente. Link
 compartilhado só com um e-mail específico não vale.

 Na própria solução ou no PDF, declare de forma explícita o que ficou
 dentro e o que ficou fora do escopo.

 Dica
 Aproveite-se do vibe coding.

 3 · Vídeo

 Até 3 minutos, mostrando a solução funcionando. Gravação de tela
 basta; produção não é avaliada. O link do vídeo também
 precisa ser público.

 Fora da avaliação
 Qualidade de código, cobertura de testes, autenticação e CI não são
 avaliados; tempo gasto ali é tempo perdido. A interface não está nessa
 lista: ela é avaliada. Como dividir o seu tempo entre a tela e o resto da
 solução é uma decisão sua — e equilibrar as duas coisas faz parte
 do trabalho.

 Preferimos um recorte pequeno que funciona de ponta a ponta a uma
 solução completa só no papel. Se algo ficou de fora por decisão sua,
 diga na seção 08 — cortar escopo com justificativa conta a favor.

 Critérios de avaliação

 Não existe resposta certa única. Soluções bem diferentes
 entre si podem ser igualmente boas.

• Que você tenha entendido o processo de negócio, e não apenas o enunciado.

• Que as decisões de desenho da solução estejam justificadas, inclusive as de não usar IA em algum ponto.

• Que a interface seja boa de usar: que a pessoa que aprova consiga decidir a partir dela, sem treinamento e sem atrito.

• Que a solução continue de pé quando o ambiente não coopera.

• Que o documento se sustente sozinho diante de quem não é técnico.

• Que a entrega esteja organizada e dentro do prazo, e que o tempo tenha sido bem distribuído entre as partes.

 Logística

• Prazo: 48 horas corridas a partir do recebimento. Prazo fixo, sem extensão.

• Escopo: o case é maior do que cabe no prazo, de propósito. Priorizar faz parte da avaliação; não tente fazer tudo.

• Ferramentas: livres. Não existe ferramenta esperada, e não tente adivinhar a nossa. Vale qualquer uma, desde que a solução fique acessível por link público.

• Uso de IA: liberado e esperado. Registre na seção 08 onde você usou e para quê.

• Entrega: exclusivamente pelo formulário da Pareto, com o PDF, o link público da solução e o link do vídeo. Cases enviados por e-mail não serão aceitos.

• Dúvidas de negócio: não haverá canal de esclarecimento. O que estiver ambíguo, decida, assuma a premissa e registre no documento. Lidar com informação incompleta é parte do que estamos avaliando.

• Problemas operacionais — arquivo corrompido, link do dataset quebrado — são exceção: responda ao e-mail pelo qual recebeu o case e resolvemos.

• Etapa seguinte: uma conversa em que você apresenta a proposta em 15 minutos para uma pessoa de negócio, e depois aprofundamos as decisões que você tomou.

 Envio do case

 PDF, link público da solução e link do vídeo, em até
 48 horas a partir do recebimento. Somente pelo formulário.

 Acessar formulário de entrega
 https://paretogroup.typeform.com/to/jQHsLgzs

Documento confidencial · uso exclusivo do processo seletivo