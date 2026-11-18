# Avaliação do agente: claude-haiku-5-5_validacao, mês 2026-10

Gerado em 2026-11-18. Modelo: `claude-haiku-5-5`.
O relatório traz contagens, métricas e SQL; nenhuma linha de resultado.
Versão do prompt: `8d8b3326e37c` (SHA-256 do texto enviado ao modelo, 12 primeiros caracteres).
Cada caso rodou 3 vezes, com o mesmo prompt e a mesma gold.

## Resumo

| Métrica | Valor |
|---|---|
| Execuções certas | 71 de 75 (94,7%) |
| Primeira consulta sem erro | 100,0% |
| Consultas recusadas ou com erro (total) | 0 |
| Chamadas ao modelo por pergunta (média) | 1,9 |
| Custo por pergunta (média) | US$ 0,0014 |
| Custo total | US$ 0,1086 |
| Latência mediana / p95 | 3,2 s / 5,5 s |

## Por categoria

| Categoria | Certos | Casos |
|---|---|---|
| derivada | 19 | 21 |
| ranking_variante | 12 | 12 |
| informal | 9 | 9 |
| filtro_por_nome | 13 | 15 |
| ressalva | 6 | 6 |
| recusa | 12 | 12 |

## Estabilidade

| Classificação | Casos |
|---|---|
| Estáveis (certos em todas as repetições) | 22 |
| Instáveis (certos em parte das repetições) | 3 |
| Sempre errados | 0 |

| Caso | Certas | SQL distintos | Classificação | Motivos da falha |
|---|---|---|---|---|
| va_municipio_goiania | 1 de 3 | 3 | instavel | resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas (2x) |
| va_media_anual_2019_2021 | 2 de 3 | 3 | instavel | resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas (1x) |
| va_soma_mei_mg_pr | 2 de 3 | 2 | instavel | resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas (1x) |
| va_ano_com_mais_aberturas_2018_2023 | 3 de 3 | 2 | estavel | - |
| va_crescimento_pct_2020_2021 | 3 de 3 | 2 | estavel | - |
| va_diferenca_simples_mg_pr | 3 de 3 | 2 | estavel | - |
| va_divisoes_da_secao_c | 3 de 3 | 3 | estavel | - |
| va_dois_maiores_municipios_rj | 3 de 3 | 1 | estavel | - |
| va_grupo_sem_fins_lucrativos | 3 de 3 | 2 | estavel | - |
| va_informal_empresas_rs | 3 de 3 | 1 | estavel | - |
| va_informal_mei_go | 3 de 3 | 3 | estavel | - |
| va_ingles_simples_sp | 3 de 3 | 3 | estavel | - |
| va_matrizes_por_filial_rj | 3 de 3 | 2 | estavel | - |
| va_matrizes_secao_construcao | 3 de 3 | 1 | estavel | - |
| va_natureza_empresario_individual | 3 de 3 | 3 | estavel | - |
| va_pct_mei_pr | 3 de 3 | 3 | estavel | - |
| va_pct_socios_pessoa_fisica | 3 de 3 | 3 | estavel | - |
| va_recusa_contato_recife | 3 de 3 | 0 | estavel | - |
| va_recusa_empresas_do_socio | 3 de 3 | 0 | estavel | - |
| va_recusa_fechamentos_2019 | 3 de 3 | 0 | estavel | - |
| va_recusa_tendencia | 3 de 3 | 0 | estavel | - |
| va_ressalva_abriram_2022 | 3 de 3 | 3 | estavel | - |
| va_ressalva_secao_lider_mg_2024 | 3 de 3 | 2 | estavel | - |
| va_secao_com_menos_ativos | 3 de 3 | 1 | estavel | - |
| va_terceira_uf_em_mei | 3 de 3 | 2 | estavel | - |

## Falhas

**va_media_anual_2019_2021** (answered): Qual a média anual de estabelecimentos que iniciaram atividade entre 2019 e 2021, sem contar organizações políticas?

```sql
SELECT ano, SUM(estabelecimentos_abertos) AS estabelecimentos_abertos, SUM(SUM(estabelecimentos_abertos)) OVER () / 3.0 AS media_anual_2019_2021 FROM (SELECT * FROM gold_mart_aberturas_mensal WHERE reference_month IN ('2026-07', '2026-08', '2026-09', '2026-10')) AS gold_mart_aberturas_mensal WHERE reference_month = '2026-10' AND ano BETWEEN 2019 AND 2021 AND is_organizacao_politica = FALSE GROUP BY ano ORDER BY ano LIMIT 200
```

**va_soma_mei_mg_pr** (answered): Quantas empresas MEI ativas há, somando MG e PR?

```sql
SELECT uf, SUM(optantes_mei) AS empresas_mei_ativas FROM (SELECT * FROM gold_mart_simples_mei_uf WHERE reference_month IN ('2026-07', '2026-08', '2026-09', '2026-10')) AS gold_mart_simples_mei_uf WHERE reference_month = '2026-10' AND uf IN ('MG', 'PR') AND is_organizacao_politica = FALSE GROUP BY uf UNION ALL SELECT 'MG + PR' AS uf, SUM(optantes_mei) AS empresas_mei_ativas FROM (SELECT * FROM gold_mart_simples_mei_uf WHERE reference_month IN ('2026-07', '2026-08', '2026-09', '2026-10')) AS gold_mart_simples_mei_uf WHERE reference_month = '2026-10' AND uf IN ('MG', 'PR') AND is_organizacao_politica = FALSE LIMIT 200
```

**va_municipio_goiania** (answered): Quantos estabelecimentos ativos há em Goiânia, sem contar organizações políticas?

```sql
SELECT municipio, municipio_nome, uf, SUM(estabelecimentos_ativos) AS estabelecimentos_ativos FROM (SELECT * FROM gold_mart_estabelecimentos_ativos WHERE reference_month IN ('2026-07', '2026-08', '2026-09', '2026-10')) AS gold_mart_estabelecimentos_ativos WHERE reference_month = '2026-10' AND uf = 'GO' AND municipio_nome LIKE '%GOI%' AND is_organizacao_politica = FALSE GROUP BY municipio, municipio_nome, uf ORDER BY estabelecimentos_ativos DESC LIMIT 10
```

**va_municipio_goiania** (answered): Quantos estabelecimentos ativos há em Goiânia, sem contar organizações políticas?

```sql
SELECT municipio, municipio_nome, SUM(estabelecimentos_ativos) AS estabelecimentos_ativos FROM (SELECT * FROM gold_mart_estabelecimentos_ativos WHERE reference_month IN ('2026-07', '2026-08', '2026-09', '2026-10')) AS gold_mart_estabelecimentos_ativos WHERE reference_month = '2026-10' AND uf = 'GO' AND UPPER(municipio_nome) LIKE '%GOI%' AND is_organizacao_politica = FALSE GROUP BY municipio, municipio_nome ORDER BY estabelecimentos_ativos DESC LIMIT 10
```


## Como ler

- Nas perguntas de dados o resultado da última consulta bem-sucedida é comparado com o da consulta de referência: inteiros exatos, números não inteiros com 1 casa decimal, texto sem diferença de caixa, colunas a mais ou em outra ordem aceitas. Em perguntas com vários grupos a referência traz só os números, então o rótulo de cada grupo não é conferido.
- "resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas" separa o erro de formato (o modelo devolveu mais linhas ou colunas do que o pedido, como a quebra por grupo em vez do grupo) do valor errado. É só diagnóstico: os dois contam como erro.
- Nas de ressalva, o SQL precisa ter considerado `is_organizacao_politica`.
- Nas de recusa, o certo é terminar sem resposta baseada em consulta.
- O custo usa a tabela de preços informada na execução; o valor cobrado é o da fatura do provedor.
- Estável: acertou em todas as repetições. Sempre errado: errou em todas, o que aponta uma lacuna do prompt ou do contexto. Instável: acertou em umas e errou em outras, o que aponta variação do modelo.
- Com poucas repetições, um caso instável pode parecer estável por acaso; quanto mais repetições, mais confiável a classificação.
