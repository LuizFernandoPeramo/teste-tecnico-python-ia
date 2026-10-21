
# Exercício 3c — Métrica para detectar regressão de qualidade

## Métrica escolhida

A métrica que eu priorizaria é a **taxa de escalonamento para atendimento humano**, analisada por intenção, principalmente nos casos relacionados ao cancelamento.

Também acompanharia a taxa de cancelamentos iniciados sem confirmação prévia, como indicador de segurança.

## Justificativa

Entre as três métricas apresentadas, a taxa de escalonamento é a que possui maior relação com a capacidade do chatbot de resolver as solicitações dos usuários.

A latência média indica o tempo de resposta, mas não demonstra se a orientação fornecida foi correta. Um chatbot pode responder rapidamente e, ainda assim, interpretar a solicitação de maneira equivocada.

O custo por sessão também não representa diretamente a qualidade. Uma redução no custo pode ocorrer porque as respostas ficaram mais curtas ou porque o usuário abandonou a conversa antes de resolver o problema.

Já a taxa de escalonamento permite observar situações em que o chatbot não conseguiu concluir o atendimento e precisou encaminhar o usuário para um atendente humano.

| Métrica | Relação com a qualidade | Utilização |
|---|---|---|
| Latência média | Baixa. Mede o tempo de resposta, não a precisão do atendimento | Monitorar desempenho |
| Custo por sessão | Indireta. Pode variar conforme o tamanho das respostas e a quantidade de interações | Controlar custos |
| Taxa de escalonamento | Alta. Ajuda a identificar dificuldades na resolução das solicitações | Principal indicador escolhido |

## Cuidados na interpretação

A taxa de escalonamento precisa ser analisada com alguns cuidados.

### 1. Nem todo escalonamento representa uma falha

O aumento dos encaminhamentos para atendimento humano pode indicar que o chatbot está encontrando dificuldades.

Entretanto, também pode representar um comportamento correto quando a situação exige intervenção humana.

Por isso, é importante registrar o motivo do escalonamento e comparar atendimentos com a mesma intenção.

### 2. O chatbot pode cometer erros sem escalonar

Uma situação mais grave ocorre quando o chatbot interpreta incorretamente uma solicitação e executa uma ação sem autorização.

No caso apresentado, isso pode acontecer quando o usuário escreve "quero cancelar" e o sistema inicia o cancelamento sem verificar o que realmente deve ser cancelado.

Esse problema não seria identificado apenas pela taxa de escalonamento.

Por esse motivo, também deve ser monitorada a quantidade de cancelamentos iniciados sem confirmação explícita do usuário.

A meta para esse indicador deve ser zero.

### 3. A média geral pode esconder problemas específicos

Se a falha ocorre principalmente em solicitações ambíguas de cancelamento, analisar todas as conversas em conjunto pode dificultar sua identificação.

A recomendação é acompanhar separadamente os atendimentos relacionados a cancelamento, principalmente aqueles em que o usuário não informa claramente o que deseja cancelar.

### 4. Comparação antes e depois da alteração

Para avaliar a mudança do prompt, utilizaria uma implantação gradual.

Inicialmente, o novo prompt seria disponibilizado para uma pequena parcela dos atendimentos, entre 5% e 10%, enquanto o restante continuaria utilizando a versão anterior.

Essa comparação permitiria observar possíveis diferenças na taxa de escalonamento antes de ampliar a utilização do novo prompt.

A análise deve considerar o volume de atendimentos e a variação estatística dos resultados.

## Indicadores complementares

Além da taxa de escalonamento, acompanharia os seguintes indicadores:

| Indicador | Objetivo |
|---|---|
| Cancelamentos sem confirmação | Identificar ações executadas sem autorização explícita |
| Solicitações ambíguas esclarecidas | Verificar se o chatbot faz perguntas antes de iniciar o cancelamento |
| Cancelamentos revertidos | Identificar possíveis cancelamentos realizados incorretamente |
| Latência p95 | Identificar respostas excessivamente demoradas |
| Tokens utilizados | Acompanhar o consumo após a alteração do prompt |
| Quantidade de interações por atendimento | Verificar se o usuário precisa repetir ou esclarecer muitas vezes a solicitação |
| Retorno do usuário em até 24 horas | Identificar possíveis atendimentos não resolvidos |

## Conclusão

Eu priorizaria a taxa de escalonamento para atendimento humano porque, entre as métricas apresentadas, ela é a que melhor representa as dificuldades do chatbot em resolver as solicitações.

Entretanto, essa métrica não deve ser utilizada isoladamente.

No cenário de cancelamento, o principal cuidado é garantir que nenhuma ação seja iniciada sem confirmação explícita do usuário.

Portanto, a taxa de escalonamento seria o indicador principal de acompanhamento, enquanto a taxa de cancelamentos sem confirmação funcionaria como critério de segurança para aprovação da nova versão do prompt.

Mesmo que a taxa de escalonamento permaneça estável, a identificação de cancelamentos indevidos deve impedir a ampliação da implantação até que o problema seja corrigido.
