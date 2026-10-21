
# Exercício 2 — Fluxo n8n + DLQ: falha silenciosa

Fluxo analisado:

`Schedule → SQS (Receive) → IF (válida?) → HTTP POST /reprocess → SQS Delete Message`

## a) Causas mais prováveis

### 1. Mensagens descartadas pelo ramo falso do IF

Quando uma mensagem não atende às condições de validação, o fluxo termina sem executar nenhuma ação.

Nesse cenário, a mensagem não é excluída da fila e também não gera um alerta. Após o término do visibility timeout, ela fica disponível novamente para processamento.

Esse comportamento pode se repetir indefinidamente, provocando acúmulo de mensagens na DLQ.

O problema é especialmente difícil de identificar porque o n8n pode registrar a execução como concluída com sucesso, mesmo sem realizar o reprocessamento.

Essa é uma das causas mais prováveis para o acúmulo de mensagens sem erros aparentes.

### 2. Visibility timeout insuficiente

Ao receber uma mensagem do SQS, ela permanece temporariamente invisível para outros consumidores.

Se o tempo necessário para executar a chamada HTTP for maior que o visibility timeout configurado, a mensagem poderá ficar disponível novamente antes da conclusão do processamento.

Isso pode provocar:

- Processamento duplicado da mesma mensagem.
- Novas tentativas desnecessárias.
- Falhas na exclusão da mensagem.
- Aumento do volume de mensagens pendentes.

Também é necessário garantir que a exclusão utilize o ReceiptHandle correspondente ao recebimento atual.

### 3. Tratamento inadequado dos erros HTTP

O nó HTTP Request pode estar configurado para continuar a execução mesmo quando recebe respostas de erro.

Configurações como `Continue On Fail` ou `On Error: Continue` permitem que o fluxo avance sem interromper a execução.

Dependendo da configuração, uma resposta HTTP com erro pode ser tratada como dado e encaminhada ao próximo nó.

Se o fluxo executar o Delete Message sem confirmar o sucesso do reprocessamento, a mensagem poderá ser excluída indevidamente.

Nesse caso, ocorre um problema ainda mais grave: perda de mensagens sem que o processamento tenha sido concluído.

### 4. Permissões IAM incompletas

As credenciais utilizadas pelo n8n podem possuir permissão para receber mensagens, mas não para excluí-las.

Por exemplo:

- `sqs:ReceiveMessage`: permite receber mensagens.
- `sqs:DeleteMessage`: permite excluir mensagens.
- `kms:Decrypt`: pode ser necessário quando a fila utiliza criptografia com chave KMS.

Se a exclusão falhar por falta de permissão, a mensagem permanecerá na fila e poderá ser recebida novamente.

Também devem ser verificadas a região AWS, a conta utilizada e o endereço da fila configurado no fluxo.

### 5. Limitações do recebimento de mensagens

O SQS permite receber até 10 mensagens por chamada de ReceiveMessage.

Se o fluxo executar poucas vezes por hora e a quantidade de mensagens recebidas for inferior à quantidade de mensagens que chegam à DLQ, ocorrerá acúmulo.

Outro fator é o tipo de polling utilizado.

Com short polling, uma consulta pode retornar vazia mesmo quando existem mensagens disponíveis.

O long polling, configurado por meio de `WaitTimeSeconds`, reduz a quantidade de respostas vazias e melhora a eficiência do recebimento.

### 6. Execuções concluídas sem processar mensagens

Quando o nó SQS não retorna registros, os nós seguintes podem não ser executados.

Mesmo assim, o n8n pode apresentar a execução como bem-sucedida.

Isso dificulta a identificação de problemas porque uma execução concluída não significa necessariamente que alguma mensagem foi processada.

É importante diferenciar três situações:

- Execução concluída sem mensagens disponíveis.
- Execução concluída com mensagens processadas.
- Execução concluída sem processamento por causa de uma condição ou configuração incorreta.

### 7. Falta de idempotência e mensagens inválidas

O endpoint `/reprocess` precisa considerar a possibilidade de receber a mesma mensagem mais de uma vez.

Sem idempotência, uma nova tentativa pode provocar operações duplicadas.

Outro problema ocorre quando uma mensagem foi encaminhada à DLQ porque possui dados inválidos.

Se a causa original não for corrigida, o reprocessamento poderá falhar novamente.

Sem controle de tentativas, classificação de erros e uma estratégia de tratamento, a mensagem poderá permanecer em um ciclo de falhas.

### 8. Outros problemas de configuração

Também devem ser investigados:

- Schedule desativado ou configurado com horário incorreto.
- Execuções concorrentes consumindo mensagens da mesma fila.
- Configuração incorreta do Queue URL.
- Política de retenção insuficiente.
- Credenciais associadas à conta ou região AWS incorreta.
- Configurações inadequadas de redrive.
- Falta de controle sobre a quantidade de tentativas de reprocessamento.

O SQS permite configurar retenção de mensagens por até 14 dias. Portanto, mensagens que permanecem muito tempo na DLQ podem ser removidas automaticamente quando atingem o prazo de retenção.

### Como confirmar a causa

A primeira etapa seria consultar as métricas e os atributos das mensagens no SQS.

Os principais indicadores são:

- `ApproximateNumberOfMessagesVisible`
- `ApproximateNumberOfMessagesNotVisible`
- `ApproximateReceiveCount`
- `NumberOfMessagesReceived`
- `NumberOfMessagesDeleted`

Um número elevado de recebimentos, acompanhado de poucas exclusões, pode indicar falhas no processamento, no visibility timeout ou nas permissões.

Também é importante verificar o histórico de execuções do n8n, principalmente os resultados dos nós IF, HTTP Request e Delete Message.

## b) Instrumentação sem alterar a lógica principal

O objetivo é identificar falhas mesmo quando o n8n apresenta as execuções como bem-sucedidas.

### 1. Workflow de erro global

Configurar um workflow de tratamento de erros utilizando o Error Trigger do n8n.

Esse fluxo poderá encaminhar notificações por e-mail, Slack ou outro canal de monitoramento.

O alerta deve conter informações como:

- Nome do workflow.
- Identificador da execução.
- Nó em que ocorreu o erro.
- Mensagem retornada.
- Data e horário da ocorrência.

Esse mecanismo identifica falhas explícitas, mas não é suficiente para detectar problemas silenciosos.

### 2. Alarmes no CloudWatch

Criar alarmes diretamente sobre as métricas da DLQ.

Os principais indicadores são:

| Métrica | Finalidade |
|---|---|
| ApproximateNumberOfMessagesVisible | Identificar mensagens acumuladas |
| ApproximateAgeOfOldestMessage | Identificar mensagens aguardando processamento por muito tempo |
| NumberOfMessagesReceived | Acompanhar o volume de mensagens recebidas |
| NumberOfMessagesDeleted | Verificar quantas mensagens estão sendo excluídas |

O monitoramento deve observar principalmente o crescimento contínuo da fila e o aumento da idade das mensagens.

Esses indicadores permitem detectar problemas mesmo quando o workflow não registra erros.

### 3. Registro do ramo falso do IF

Adicionar um mecanismo de registro no ramo falso do IF.

Quando uma mensagem não atender às condições de validação, o fluxo deverá registrar:

- Identificador da mensagem.
- Motivo da rejeição.
- Data e horário.
- Identificador da execução.

Dependendo da criticidade, também poderá ser enviado um alerta.

Essa alteração não modifica o processamento das mensagens válidas, mas permite identificar aquelas que estão sendo ignoradas.

### 4. Heartbeat do workflow

Implementar um mecanismo de heartbeat para indicar que o workflow continua funcionando.

O n8n poderá enviar periodicamente um sinal para um serviço de monitoramento.

Se o sinal deixar de ser recebido dentro do intervalo esperado, será gerado um alerta.

Esse mecanismo ajuda a identificar situações como:

- Schedule desativado.
- Workflow interrompido.
- Problemas na execução.
- Falhas na infraestrutura do n8n.

É importante distinguir o heartbeat de execução do heartbeat de processamento, pois o fluxo pode continuar executando sem consumir mensagens.

### 5. Métricas por execução

Registrar indicadores operacionais ao final das execuções.

Sugestão de métricas:

- `recebidas`
- `validas`
- `reprocessadas_ok`
- `falhas_http`
- `deletadas`

Essas informações podem ser encaminhadas ao CloudWatch, Prometheus ou Datadog.

Um exemplo de condição para alerta seria uma execução que recebeu mensagens, mas não conseguiu concluir nenhuma exclusão.

Também é importante observar execuções que recebem repetidamente o limite máximo de mensagens, pois isso pode indicar que o fluxo não está acompanhando o volume da fila.

### 6. Validação explícita do resultado HTTP

Configurar o nó HTTP Request para disponibilizar o código de status da resposta.

Em seguida, utilizar uma condição para diferenciar sucesso e falha.

O caminho de sucesso deverá continuar para a exclusão da mensagem somente após a confirmação de processamento pelo endpoint.

O caminho de falha deverá registrar o erro e impedir a exclusão.

Um código HTTP 200, isoladamente, não garante que a operação foi concluída corretamente. O contrato da API deve estabelecer o que representa uma resposta bem-sucedida.

### 7. Identificadores de correlação

Utilizar o MessageId do SQS ou um correlation_id para acompanhar cada mensagem durante o processamento.

Esse identificador deverá aparecer:

- Nos registros do n8n.
- Na requisição enviada ao endpoint.
- Nos logs da aplicação responsável pelo reprocessamento.
- Nos registros de conclusão ou falha.

Isso facilita a investigação de mensagens específicas.

### 8. Retenção do histórico de execuções

Configurar o armazenamento do histórico do n8n por um período adequado às necessidades de auditoria.

É importante preservar informações suficientes para investigar falhas, inclusive quando as execuções forem registradas como bem-sucedidas.

### 9. Dashboard operacional

Concentrar os principais indicadores em um painel.

O dashboard deverá apresentar:

- Quantidade de mensagens na DLQ.
- Idade da mensagem mais antiga.
- Quantidade de mensagens recebidas.
- Quantidade de mensagens reprocessadas.
- Quantidade de falhas HTTP.
- Quantidade de mensagens excluídas.
- Situação do heartbeat.

O monitoramento deve considerar o resultado no SQS e no sistema de destino, e não apenas o status das execuções do n8n.

## c) Quando substituir o n8n por uma solução nativa AWS

A substituição do n8n por uma arquitetura baseada em AWS Lambda e EventBridge Pipes deve considerar os requisitos de volume, confiabilidade, segurança e manutenção.

### Cenários favoráveis à migração

**Volume elevado de mensagens**

Quando a DLQ recebe muitas mensagens e precisa ser esvaziada rapidamente, o processamento periódico do n8n pode se tornar insuficiente.

Uma solução baseada em serviços AWS pode oferecer maior capacidade de escalabilidade.

**Necessidade de maior controle sobre falhas**

Aplicações críticas podem exigir controle detalhado sobre tentativas de processamento, idempotência, classificação de erros e tratamento de mensagens que continuam falhando.

**Requisitos de disponibilidade**

Se o n8n estiver hospedado em uma única instância, problemas nessa infraestrutura podem interromper todo o reprocessamento.

Uma arquitetura gerenciada pela AWS pode reduzir parte dessa responsabilidade operacional.

**Segurança e conformidade**

Quando as mensagens contêm informações sensíveis, pode ser interessante manter o processamento dentro da infraestrutura AWS.

Isso também permite utilizar permissões IAM e mecanismos de auditoria integrados.

**Lógica de reprocessamento complexa**

Quando o fluxo exige regras avançadas, como backoff exponencial, classificação de erros, circuit breaker ou validações específicas, uma implementação em código pode facilitar testes e versionamento.

**Integração com serviços AWS**

Quando o sistema de destino já utiliza Lambda, Step Functions ou outros serviços AWS, a integração nativa pode reduzir a quantidade de componentes envolvidos.

### Critérios para tomar a decisão

Antes da migração, devem ser avaliados:

1. Volume de mensagens e tempo máximo aceitável para reprocessamento.
2. Necessidade de idempotência e garantias de entrega.
3. Custo da infraestrutura e da manutenção.
4. Recursos de monitoramento e auditoria.
5. Experiência da equipe responsável pela operação.
6. Quantidade de integrações com sistemas externos.
7. Necessidade de aprovação ou intervenção humana.

### Quando manter o n8n

O n8n continua sendo uma opção adequada quando:

- O volume de mensagens é baixo.
- O reprocessamento ocorre de forma manual ou semiautomática.
- Existem muitas integrações com sistemas externos.
- A equipe precisa modificar os fluxos com frequência.
- Há necessidade de aprovação humana antes de determinadas operações.

Nesses casos, a recomendação é manter o n8n e implementar os mecanismos de observabilidade apresentados no item anterior.

### Arquitetura nativa proposta

Uma possível arquitetura seria:

`DLQ → EventBridge Pipe → Lambda de reprocessamento → Sistema de destino`

O EventBridge Pipe pode realizar a integração entre a fila e o serviço de processamento, com filtros quando necessário.

A Lambda ficaria responsável por validar a mensagem, executar o reprocessamento e registrar os resultados.

Também seria necessário definir políticas adequadas de retry, tratamento de falhas e encaminhamento de mensagens que não puderem ser processadas.

Outra possibilidade seria utilizar o redrive nativo do SQS, por meio de `StartMessageMoveTask`, após a correção da causa original das falhas.

Uma fila adicional de retenção, conhecida como parking lot, poderia armazenar mensagens que continuassem apresentando problemas.

A configuração concreta dependeria dos requisitos da aplicação e precisaria ser validada em ambiente AWS.

## Cuidados na implementação

A exclusão de uma mensagem da fila deve ocorrer somente após a confirmação de sucesso do processamento no sistema de destino.

O ReceiptHandle utilizado para excluir a mensagem deve corresponder ao recebimento atual.

Também é necessário considerar que a utilização de EventBridge Pipes não elimina automaticamente os problemas de duplicidade, ordenação ou mensagens inválidas.

Essas situações devem ser tratadas de acordo com as características da fila, do serviço de processamento e do destino.

A proposta de migração apresentada é uma recomendação arquitetural. Não foi realizada validação dessa configuração em uma infraestrutura AWS real.

## Conclusão

O principal problema do fluxo apresentado é a possibilidade de ocorrerem falhas sem que o n8n registre a execução como malsucedida.

O ramo falso do IF, as configurações de timeout, o tratamento das respostas HTTP e as permissões de exclusão são os primeiros pontos que devem ser investigados.

A instrumentação proposta permite identificar mensagens acumuladas, falhas de reprocessamento e interrupções do fluxo.

A migração para uma arquitetura nativa AWS pode ser considerada quando houver maior volume, requisitos de disponibilidade mais rigorosos ou necessidade de controle operacional mais detalhado.

Para cenários simples, o n8n pode continuar sendo utilizado, desde que existam mecanismos adequados de monitoramento e tratamento de falhas.
