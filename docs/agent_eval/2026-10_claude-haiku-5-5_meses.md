# Avaliação do agente: claude-haiku-5-5_meses, mês 2026-10

Gerado em 2026-11-18. Modelo: `claude-haiku-5-5`.
O relatório traz contagens, métricas e SQL; nenhuma linha de resultado.
Versão do prompt: `8d8b3326e37c` (SHA-256 do texto enviado ao modelo, 12 primeiros caracteres).

## Resumo

| Métrica | Valor |
|---|---|
| Casos certos | 6 de 6 (100,0%) |
| Primeira consulta sem erro | 100,0% |
| Consultas recusadas ou com erro (total) | 0 |
| Chamadas ao modelo por pergunta (média) | 1,8 |
| Custo por pergunta (média) | US$ 0,0014 |
| Custo total | US$ 0,0082 |
| Latência mediana / p95 | 2,8 s / 5,0 s |

## Por categoria

| Categoria | Certos | Casos |
|---|---|---|
| varios_meses | 6 | 6 |

## Casos

| Caso | Resultado | Chamadas | Consultas | Custo (US$) | Motivo da falha |
|---|---|---|---|---|---|
| meses_ativas_sp | certo | 2 | 1 | 0,0014 | - |
| meses_ganho_uf | certo | 2 | 1 | 0,0018 | - |
| meses_total_por_mes | certo | 2 | 1 | 0,0015 | - |
| meses_mes_unico | certo | 2 | 1 | 0,0014 | - |
| meses_recusa_previsao | certo | 1 | 0 | 0,0007 | - |
| meses_sem_mes_usa_o_padrao | certo | 2 | 1 | 0,0014 | - |

## Como ler

- Nas perguntas de dados o resultado da última consulta bem-sucedida é comparado com o da consulta de referência: inteiros exatos, números não inteiros com 1 casa decimal, texto sem diferença de caixa, colunas a mais ou em outra ordem aceitas. Em perguntas com vários grupos a referência traz só os números, então o rótulo de cada grupo não é conferido.
- "resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas" separa o erro de formato (o modelo devolveu mais linhas ou colunas do que o pedido, como a quebra por grupo em vez do grupo) do valor errado. É só diagnóstico: os dois contam como erro.
- Nas de ressalva, o SQL precisa ter considerado `is_organizacao_politica`.
- Nas de recusa, o certo é terminar sem resposta baseada em consulta.
- O custo usa a tabela de preços informada na execução; o valor cobrado é o da fatura do provedor.
