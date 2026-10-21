
# Exercício 1 — Diagnóstico e correção

## Suposições

- O Event Source Mapping entre SQS e Lambda utiliza uma fila standard e pode ser configurado com `ReportBatchItemFailures`.
- O contexto das conversas é armazenado no DynamoDB, com chave no formato `session#<id>` e controle de expiração por TTL.
- O histórico de cada sessão contém mensagens com os campos `role` e `content`.
- A resposta ao usuário seria entregue por um canal assíncrono, como uma fila de respostas ou WebSocket. No código apresentado, essa entrega é simulada pela interface `ResponseSink`, que registra a operação em log.
- As variáveis `QUEUE_URL` e o cliente SQS presentes na versão original foram removidos por não serem utilizados. A exclusão das mensagens da fila é responsabilidade do Event Source Mapping da Lambda.

## a) Problemas encontrados e impacto em produção

| Nº | Problema identificado | Impacto em produção | Solução adotada |
|---|---|---|---|
| 1 | Ausência de tratamento individual das mensagens do lote | Uma falha pode provocar o reprocessamento de mensagens já concluídas, aumentando custos, duplicidades e sobrecarga | Implementação de `batchItemFailures`, retornando somente os identificadores das mensagens com falha |
| 2 | Criação de clientes boto3 dentro do handler | Aumenta o tempo de inicialização das operações e reduz o reaproveitamento das conexões | Inicialização dos clientes com `lru_cache`, permitindo reutilização entre invocações no mesmo ambiente |
| 3 | Ausência de configuração explícita de timeout e retry | Chamadas demoradas podem consumir o tempo disponível da Lambda e provocar novas tentativas de processamento | Configuração de timeouts, retries adaptativos e verificação do tempo restante |
| 4 | Falta de contexto de sessão | O modelo recebe apenas a mensagem atual, comprometendo a continuidade da conversa | Recuperação do histórico e montagem das mensagens por meio de `SessionStore` e `build_messages` |
| 5 | Ausência de system prompt | As orientações gerais de comportamento não são encaminhadas ao modelo | Inclusão do campo `system` nas chamadas ao Bedrock |
| 6 | Resposta apenas exibida no log | O processamento não possui um mecanismo de entrega ao usuário | Criação da interface `ResponseSink`, com implementação simulada |
| 7 | Falta de observabilidade estruturada | Dificulta a identificação de falhas, gargalos e consumo de recursos | Logs JSON com identificadores de correlação e métricas no formato CloudWatch EMF |
| 8 | Ausência de validação das mensagens recebidas | Mensagens inválidas podem provocar falhas repetidas e consumo desnecessário de recursos | Validação dos campos obrigatórios e utilização de `InvalidMessageError` |
| 9 | Tratamento insuficiente dos erros do modelo | Dificulta a identificação de falhas transitórias e permanentes | Classificação dos erros por meio de `ModelInvocationError` e registro de métricas |
| 10 | Limite fixo de 2048 tokens | Pode aumentar a latência e o custo das respostas | Redução do valor padrão para 512 tokens, mantendo configuração por variável de ambiente |
| 11 | Ausência de idempotência | Mensagens duplicadas podem gerar processamento e respostas repetidas | Controle de mensagens processadas e reserva de processamento |

O modelo configurado originalmente, `anthropic.claude-3-sonnet-20240229-v1:0`, pertence a uma geração anterior. O identificador foi mantido configurável pela variável `BEDROCK_MODEL_ID`, permitindo sua substituição sem alterações na lógica do programa.

## b) Correções implementadas

A implementação foi organizada principalmente nos arquivos:

- `codigo_corrigido.py`: implementação do consumidor Lambda, com simulação local.
- `test_codigo_corrigido.py`: testes automatizados utilizando pytest.

As principais correções foram:

1. Tratamento individual das mensagens de cada lote, permitindo que apenas os registros com falha retornem para a fila.
2. Recuperação do histórico da sessão e envio do contexto ao modelo.
3. Inclusão de system prompt nas chamadas ao Bedrock.
4. Registro de logs estruturados e métricas de desempenho.
5. Controle de idempotência para evitar o processamento de mensagens já concluídas.
6. Separação das responsabilidades de armazenamento, geração de respostas e entrega.
7. Verificação do tempo restante da Lambda antes de iniciar novas chamadas.
8. Liberação da reserva de processamento quando ocorre uma falha.

A arquitetura utiliza as interfaces `SessionStore` e `ResponseSink`, facilitando a substituição das implementações e a realização de testes isolados.

A simulação local pode ser executada com:

```bash
python codigo_corrigido.py
```

Os testes automatizados podem ser executados com:

```bash
python -m pytest -v
```

Na validação local realizada durante o desenvolvimento, os 21 testes foram aprovados.

### Configurações necessárias na AWS

A implementação depende de algumas configurações de infraestrutura para funcionar corretamente em produção.

1. Configurar `FunctionResponseTypes` com o valor `ReportBatchItemFailures` no Event Source Mapping.
2. Definir o visibility timeout da fila considerando o timeout da Lambda. Como referência, a AWS recomenda um valor de pelo menos seis vezes o timeout da função.
3. Configurar uma Dead Letter Queue e definir uma política de redrive com `maxReceiveCount` adequado, por exemplo, entre 3 e 5 tentativas.
4. Utilizar lotes pequenos, inicialmente entre 1 e 5 mensagens, considerando que o processamento apresentado é sequencial.
5. Avaliar o uso de SQS FIFO com `MessageGroupId` associado ao identificador da sessão para preservar a ordem das mensagens.
6. Ajustar os limites de concorrência da Lambda de acordo com a capacidade disponível no Bedrock.

### Limitações conhecidas

A implementação inclui mecanismos de reserva condicional no DynamoDB para reduzir o risco de processamento simultâneo da mesma mensagem.

Entretanto, isso não garante entrega exatamente uma vez.

Caso a resposta seja entregue ao usuário e a Lambda apresente uma falha antes de registrar a conclusão, uma nova tentativa poderá realizar outra entrega.

Além disso, a atualização do histórico utiliza leitura seguida de escrita. Essa operação não é atômica e pode provocar perda de atualizações quando diferentes execuções modificam a mesma sessão simultaneamente.

A implementação do DynamoDB e o mecanismo de entrega ao usuário não foram validados em uma infraestrutura AWS real.

## c) Comparação entre invoke_model e invoke_model_with_response_stream

O Amazon Bedrock disponibiliza diferentes formas de invocação dos modelos.

A operação `invoke_model` aguarda a geração completa da resposta antes de disponibilizar seu conteúdo.

Já `invoke_model_with_response_stream` permite receber a resposta gradualmente, conforme os tokens são produzidos.

### Benefícios do streaming

O principal benefício é a redução do tempo até a disponibilização do primeiro conteúdo ao usuário, conhecido como Time to First Token (TTFT).

Em aplicações conversacionais, isso pode melhorar a percepção de velocidade, principalmente quando as respostas são extensas.

### Limitações no cenário apresentado

O consumidor desenvolvido recebe mensagens do SQS e processa as solicitações de maneira assíncrona.

Nesse cenário, a utilização de streaming exige cuidados adicionais:

- O consumidor SQS não mantém uma conexão direta com o usuário. Para aproveitar o streaming, seria necessário um canal de entrega incremental, como WebSocket.
- O envio de respostas parciais aumenta a complexidade do tratamento de falhas.
- Se a conexão for interrompida durante a geração, parte da resposta poderá ter sido entregue antes da falha.
- Uma nova tentativa de processamento poderá provocar duplicação do conteúdo recebido.
- O histórico da conversa precisaria considerar respostas parcialmente geradas.
- As métricas de consumo e o motivo de encerramento da geração devem ser coletados ao final do stream.
- A aplicação precisaria controlar o envio de conteúdo antes da conclusão das verificações de segurança aplicáveis.

O streaming melhora principalmente a latência percebida. Ele não elimina necessariamente o tempo total de processamento nem reduz automaticamente o custo de execução da Lambda.

### Decisão técnica

Para a arquitetura apresentada, a opção mais adequada inicialmente é manter `invoke_model`.

Essa decisão considera que o processamento é assíncrono e que a entrega da resposta ocorre somente após a conclusão da geração.

Antes de implementar streaming, as prioridades seriam:

1. Reutilizar os clientes AWS.
2. Ajustar os limites de tokens.
3. Configurar corretamente timeouts e retries.
4. Monitorar a latência das chamadas ao modelo.
5. Avaliar limites de concorrência e possíveis situações de throttling.
6. Considerar modelos menores para solicitações mais simples.

A adoção de streaming passa a fazer mais sentido caso a aplicação implemente um canal de comunicação incremental com o usuário e as medições indiquem que a latência percebida continua sendo um problema relevante.

## Revisão de concorrência e idempotência

Foi implementada uma reserva condicional no DynamoDB para reduzir o risco de duas execuções processarem simultaneamente a mesma mensagem.

O registro de controle utiliza os estados:

- `PROCESSING`: mensagem reservada para processamento.
- `COMPLETED`: mensagem processada com sucesso.

Cada reserva possui um identificador de propriedade.

As operações de conclusão e liberação verificam esse identificador, impedindo que uma execução antiga modifique uma reserva assumida por outra execução.

O tempo de reserva foi definido inicialmente em 120 segundos, apenas como referência. Em produção, esse valor deve ser ajustado conforme o timeout da Lambda, o tempo máximo de processamento e a política de reentrega da fila.

### Garantias e limitações

O mecanismo reduz a possibilidade de processamento concorrente da mesma mensagem, mas não garante entrega exatamente uma vez.

Para obter maior confiabilidade, o sistema de destino também deve reconhecer uma chave de idempotência estável.

Dependendo dos requisitos, pode ser necessário implementar uma estratégia de outbox ou persistência dos estados intermediários da entrega.

O histórico armazenado no DynamoDB continua sujeito a condições de corrida caso diferentes execuções atualizem a mesma sessão.

Para reduzir esse risco, recomenda-se utilizar SQS FIFO com `MessageGroupId` por sessão ou implementar controle de versão nas operações de atualização.

Essas garantias ainda dependem de validação em ambiente AWS.

## Integração com o Exercício 3

O prompt desenvolvido no terceiro exercício pode ser utilizado pelo consumidor Lambda.

Para isso, o arquivo `prompt_operacional.txt` deve ser incluído no pacote da função e a variável de ambiente `SYSTEM_PROMPT_FILE` deve apontar para seu caminho.

O comportamento padrão do código permanece independente, permitindo que os exercícios sejam testados separadamente.

## Considerações finais

As alterações propostas tratam os principais problemas identificados na implementação original: reprocessamento desnecessário, ausência de contexto, falta de idempotência, tratamento insuficiente de falhas e baixa observabilidade.

A solução também separa as responsabilidades do sistema, facilitando testes e futuras integrações.

Os testes locais e a simulação demonstram o funcionamento dos componentes avaliados, mas não substituem os testes de integração com SQS, Lambda, DynamoDB e Bedrock em um ambiente AWS real.
