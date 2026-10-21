
# Avaliação Técnica — Desenvolvedor Go | Python + IA

Este repositório apresenta a resolução em Python dos três exercícios da avaliação técnica.

O projeto inclui uma implementação de referência para processamento de mensagens com AWS Lambda e SQS, uma análise de reprocessamento de mensagens em DLQ utilizando n8n e uma proposta de melhoria de prompt para atendimento com inteligência artificial.

Os testes locais não exigem credenciais AWS.

## Estrutura do projeto

```text
teste-tecnico/
├── exercicio-1/
│   ├── codigo_corrigido.py
│   ├── diagnostico.md
│   ├── requirements.txt
│   └── test_codigo_corrigido.py
├── exercicio-2/
│   └── analise.md
├── exercicio-3/
│   ├── system_prompt.md
│   ├── prompt_operacional.txt
│   ├── casos_de_teste.md
│   └── analise_metricas.md
├── .gitignore
└── README.md
```

## Como executar

É necessário ter Python 3.10 ou superior instalado.

No terminal, acesse a pasta do primeiro exercício e instale as dependências:

```bash
cd exercicio-1
python -m pip install -r requirements.txt
```

Execute os testes automatizados:

```bash
python -m pytest -q
```

Para executar a demonstração local:

```bash
python codigo_corrigido.py
```

Os testes utilizam um cliente Bedrock simulado e armazenamento em memória.

A demonstração processa três mensagens, incluindo uma situação de throttling, e apresenta o resultado no formato `batchItemFailures`.

O código também contém uma implementação de referência para armazenamento no DynamoDB, mas essa integração não foi validada em um ambiente AWS real.

### Integração do prompt

Para utilizar o prompt do Exercício 3 na Lambda, inclua o arquivo `prompt_operacional.txt` no pacote da função e configure a variável de ambiente `SYSTEM_PROMPT_FILE` com o caminho correspondente.

O backend deve validar as confirmações do usuário antes de executar qualquer cancelamento.

## Exercício 1 — Integração AWS

O primeiro exercício analisa um consumidor de mensagens SQS executado por uma função AWS Lambda, responsável por encaminhar solicitações a um modelo no Amazon Bedrock.

O código original apresentava problemas no tratamento de falhas, no controle de tempo de execução e na utilização do contexto das conversas.

A implementação revisada inclui:

- Tratamento individual das falhas em lotes de mensagens.
- Reutilização dos clientes AWS.
- Configuração de timeouts e tentativas de processamento.
- Validação dos dados recebidos.
- Recuperação do histórico das sessões.
- Logs estruturados e métricas de execução.
- Controle de idempotência e reserva condicional de processamento.

O mecanismo de reserva utiliza um identificador de propriedade para reduzir conflitos entre execuções concorrentes.

Entretanto, o SQS trabalha com entrega de mensagens pelo menos uma vez. Por isso, a integração real responsável pela entrega das respostas também precisa implementar idempotência.

Para o tratamento parcial de falhas funcionar corretamente, o Event Source Mapping da Lambda deve estar configurado com `ReportBatchItemFailures`.

A análise completa, incluindo a comparação entre `invoke_model` e streaming, está disponível em `exercicio-1/diagnostico.md`.

## Exercício 2 — n8n e DLQ

O segundo exercício investiga um fluxo de reprocessamento de mensagens de uma Dead Letter Queue utilizando n8n.

A análise considera possíveis causas para o acúmulo de mensagens sem erros aparentes, incluindo:

- Configuração inadequada do visibility timeout.
- Falhas no tratamento das respostas HTTP.
- Permissões IAM insuficientes.
- Mensagens inválidas.
- Processamento duplicado.
- Execuções concluídas sem efetivamente processar mensagens.

A proposta prioriza a implementação de mecanismos de observabilidade, como alarmes no CloudWatch, registros de correlação e acompanhamento do volume de mensagens na fila.

Também são apresentados critérios para avaliar uma possível migração para serviços nativos da AWS, como Lambda e EventBridge Pipes.

A análise completa está disponível em `exercicio-2/analise.md`.

## Exercício 3 — Prompt e avaliação

O terceiro exercício propõe melhorias no comportamento de um chatbot de atendimento ao cliente.

O principal objetivo é evitar que solicitações ambíguas provoquem cancelamentos indevidos.

O prompt estabelece três condições obrigatórias antes de iniciar um cancelamento:

1. Identificar o item que o cliente deseja cancelar.
2. Confirmar que existe intenção explícita de cancelamento.
3. Solicitar e receber confirmação clara do cliente.

Também foram definidos critérios para encaminhamento ao atendimento humano, tratamento de insatisfação e respostas fora do escopo.

O plano de testes contempla situações como solicitações ambíguas, mudanças de decisão, múltiplos itens e tentativas de ignorar as instruções do chatbot.

Para acompanhar possíveis regressões após alterações no prompt, foi priorizada a taxa de escalonamento para atendimento humano, segmentada por intenção.

Esse indicador deve ser acompanhado por uma regra de segurança: nenhum cancelamento pode ser iniciado sem confirmação válida.

As ações propostas pelo modelo devem ser verificadas pelo backend antes de qualquer execução real.

## Qual problema resolver primeiro?

Eu começaria pelo consumidor SQS/Lambda.

As falhas de timeout e o reprocessamento de lotes completos podem atingir vários usuários, aumentar o consumo do Bedrock e ampliar o volume de mensagens encaminhadas à DLQ.

As primeiras ações seriam:

1. Habilitar o tratamento parcial de falhas.
2. Ajustar os tempos de execução e de visibilidade das mensagens.
3. Monitorar latência, erros e tentativas de processamento.
4. Configurar um alarme para identificar acúmulo de mensagens na DLQ.

O problema relacionado ao prompt também exige atenção, principalmente pelo risco de cancelamentos indevidos.

Entretanto, a implantação das melhorias no chatbot deve ser acompanhada de testes de comportamento e validações no backend.

## Limitações

Esta implementação foi desenvolvida como referência técnica e possui algumas limitações:

- O armazenamento no DynamoDB não foi validado em uma conta AWS real.
- O histórico das sessões ainda exige controle de concorrência para evitar perda de atualizações.
- A entrega das respostas é simulada por logs, sem integração real com WebSocket, webhook ou fila de saída.
- A reserva de processamento utiliza um tempo de expiração demonstrativo e não implementa renovação automática.
- O modelo Bedrock é configurável, mas sua disponibilidade e as permissões necessárias não foram verificadas.
- A análise do fluxo n8n é documental e não representa uma execução validada em produção.
- Os testes de comportamento do prompt foram propostos, mas não executados contra um modelo real.

## Considerações finais

Os três exercícios abordam aspectos complementares de uma aplicação que utiliza inteligência artificial: confiabilidade no processamento, monitoramento operacional e segurança das ações realizadas pelo chatbot.

A proposta busca melhorar a previsibilidade do sistema, facilitar a identificação de falhas e reduzir o risco de operações indevidas.

As soluções apresentadas podem servir como base para uma implementação em produção, desde que sejam realizadas as validações de integração e os ajustes necessários à infraestrutura utilizada.

## Uso de IA

Foram utilizadas ferramentas de IA para elaborar a primeira versão da solução e apoiar sua revisão técnica. O material inicial foi produzido com Claude; a revisão posterior foi realizada com ChatGPT. O auxílio incluiu a organização dos arquivos, a implementação Python, a elaboração dos testes e a redação das análises.


### Partes da solução em que a IA auxiliou

Exercício 1 — AWS Lambda, SQS e Bedrock

A IA auxiliou na identificação de problemas do código original e na elaboração de melhorias relacionadas ao tratamento de erros, processamento de mensagens, controle de duplicidade, gerenciamento do histórico das conversas e observabilidade.

Também foi utilizada na elaboração e revisão dos testes automatizados com pytest.

Exercício 2 — Análise do fluxo n8n

Utilizei a IA para apoiar a análise das possíveis causas de falhas no reprocessamento de mensagens, discutir alternativas de monitoramento e organizar as recomendações técnicas.

Exercício 3 — Engenharia de prompts

A IA auxiliou na estruturação do prompt, na definição de cenários de teste e na escolha de métricas para avaliar o comportamento do assistente virtual, especialmente em situações que exigem confirmação do cliente ou encaminhamento para atendimento humano.

Documentação

Utilizei a ferramenta para revisar a redação, organizar as explicações e melhorar a apresentação dos arquivos Markdown e do README.

### Sugestões modificadas ou rejeitadas

Durante a revisão, fiz ajustes nas sugestões apresentadas pela IA para adequá-las aos requisitos do desafio.

- Tratamento de falhas: mantive a abordagem de falhas parciais do SQS, permitindo que somente as mensagens com erro sejam encaminhadas para uma nova tentativa.
- Ambiente de execução: optei por utilizar simulações locais do Bedrock e do armazenamento de sessões, em vez de depender de uma infraestrutura AWS real para executar os testes.
- Organização do código: retirei comentários e explicações extensas dos arquivos Python, mantendo a documentação técnica em arquivos separados.
- Documentação: revisei e simplifiquei os textos gerados para apresentar as decisões técnicas de forma mais objetiva.
- Validação: não considerei as sugestões da IA como suficientes por si só. Executei os testes automatizados e a demonstração local para verificar o comportamento da implementação.

### Validação da solução

Após os ajustes, executei os testes automatizados com pytest, obtendo 21 testes aprovados.

Também executei a demonstração local do consumidor SQS, verificando o processamento de três mensagens e o retorno de apenas uma mensagem com falha no campo `batchItemFailures`.

Os testes foram realizados com componentes simulados. Portanto, os resultados comprovam o comportamento nos cenários locais avaliados, mas não substituem uma validação integrada em ambiente AWS.

A IA foi utilizada como ferramenta de apoio ao desenvolvimento, e suas sugestões passaram por revisão, ajustes e testes antes de serem incorporadas à solução.