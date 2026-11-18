# Avaliação do agente: claude-haiku-5-5_validacao, mês 2026-10

Gerado em 2026-11-18. Modelo: `claude-haiku-5-5`.
O relatório traz contagens, métricas e SQL; nenhuma linha de resultado.
Versão do prompt: `5d9763a8110e` (SHA-256 do texto enviado ao modelo, 12 primeiros caracteres).

## Resumo

| Métrica | Valor |
|---|---|
| Casos certos | 22 de 25 (88,0%) |
| Primeira consulta sem erro | 100,0% |
| Consultas recusadas ou com erro (total) | 0 |
| Chamadas ao modelo por pergunta (média) | 2,0 |
| Custo por pergunta (média) | US$ 0,0015 |
| Custo total | US$ 0,0382 |
| Latência mediana / p95 | 3,3 s / 5,9 s |

## Por categoria

| Categoria | Certos | Casos |
|---|---|---|
| derivada | 7 | 7 |
| ranking_variante | 4 | 4 |
| informal | 3 | 3 |
| filtro_por_nome | 3 | 5 |
| ressalva | 2 | 2 |
| recusa | 3 | 4 |

## Casos

| Caso | Resultado | Chamadas | Consultas | Custo (US$) | Motivo da falha |
|---|---|---|---|---|---|
| va_pct_mei_pr | certo | 2 | 1 | 0,0014 | - |
| va_diferenca_simples_mg_pr | certo | 2 | 1 | 0,0016 | - |
| va_media_anual_2019_2021 | certo | 2 | 1 | 0,0016 | - |
| va_crescimento_pct_2020_2021 | certo | 2 | 1 | 0,0018 | - |
| va_matrizes_por_filial_rj | certo | 2 | 1 | 0,0018 | - |
| va_pct_socios_pessoa_fisica | certo | 2 | 1 | 0,0015 | - |
| va_soma_mei_mg_pr | certo | 2 | 1 | 0,0014 | - |
| va_terceira_uf_em_mei | certo | 2 | 1 | 0,0014 | - |
| va_ano_com_mais_aberturas_2018_2023 | certo | 2 | 1 | 0,0016 | - |
| va_dois_maiores_municipios_rj | certo | 2 | 1 | 0,0015 | - |
| va_secao_com_menos_ativos | certo | 2 | 1 | 0,0016 | - |
| va_ingles_simples_sp | certo | 2 | 1 | 0,0014 | - |
| va_informal_empresas_rs | certo | 2 | 1 | 0,0014 | - |
| va_informal_mei_go | certo | 2 | 1 | 0,0015 | - |
| va_municipio_goiania | errado | 3 | 2 | 0,0024 | resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas |
| va_matrizes_secao_construcao | certo | 2 | 1 | 0,0015 | - |
| va_natureza_empresario_individual | errado | 2 | 1 | 0,0016 | resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas |
| va_grupo_sem_fins_lucrativos | certo | 2 | 1 | 0,0015 | - |
| va_divisoes_da_secao_c | certo | 2 | 1 | 0,0014 | - |
| va_ressalva_abriram_2022 | certo | 2 | 1 | 0,0016 | - |
| va_ressalva_secao_lider_mg_2024 | certo | 2 | 2 | 0,0016 | - |
| va_recusa_empresas_do_socio | certo | 1 | 0 | 0,0007 | - |
| va_recusa_tendencia | errado | 3 | 2 | 0,0027 | deveria recusar, mas o estado foi answered |
| va_recusa_fechamentos_2019 | certo | 1 | 0 | 0,0008 | - |
| va_recusa_contato_recife | certo | 1 | 0 | 0,0007 | - |

## Falhas

**va_municipio_goiania** (answered): Quantos estabelecimentos ativos há em Goiânia, sem contar organizações políticas?

```sql
SELECT municipio, municipio_nome, SUM(estabelecimentos_ativos) AS estabelecimentos_ativos FROM (SELECT * FROM gold_mart_estabelecimentos_ativos WHERE reference_month IN ('2026-07', '2026-08', '2026-09', '2026-10')) AS gold_mart_estabelecimentos_ativos WHERE reference_month = '2026-10' AND uf = 'GO' AND UPPER(municipio_nome) LIKE '%GOI%' AND is_organizacao_politica = FALSE GROUP BY municipio, municipio_nome LIMIT 200
```

**va_natureza_empresario_individual** (answered): Quantas empresas ativas têm a natureza jurídica Empresário (Individual)?

```sql
SELECT natureza_juridica, natureza_descricao, SUM(empresas_ativas) AS empresas_ativas FROM (SELECT * FROM gold_mart_perfil_porte_natureza WHERE reference_month IN ('2026-07', '2026-08', '2026-09', '2026-10')) AS gold_mart_perfil_porte_natureza WHERE reference_month = '2026-10' AND is_organizacao_politica = FALSE AND natureza_descricao LIKE '%Individual%' GROUP BY natureza_juridica, natureza_descricao ORDER BY empresas_ativas DESC LIMIT 200
```

**va_recusa_tendencia** (answered): O Brasil vai ter mais MEIs no ano que vem?

```sql
SELECT SUM(CASE WHEN reference_month = '2026-10' THEN optantes_mei END) - SUM(CASE WHEN reference_month = '2026-07' THEN optantes_mei END) AS variacao_mei_jul_a_out, SUM(CASE WHEN reference_month = '2026-10' THEN optantes_mei END) AS mei_2026_10, SUM(CASE WHEN reference_month = '2026-07' THEN optantes_mei END) AS mei_2026_07 FROM (SELECT * FROM gold_mart_simples_mei_uf WHERE reference_month IN ('2026-07', '2026-08', '2026-09', '2026-10')) AS gold_mart_simples_mei_uf WHERE is_organizacao_politica = FALSE AND reference_month IN ('2026-07', '2026-10') LIMIT 200
```


## Como ler

- Nas perguntas de dados o resultado da última consulta bem-sucedida é comparado com o da consulta de referência: inteiros exatos, números não inteiros com 1 casa decimal, texto sem diferença de caixa, colunas a mais ou em outra ordem aceitas. Em perguntas com vários grupos a referência traz só os números, então o rótulo de cada grupo não é conferido.
- "resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas" separa o erro de formato (o modelo devolveu mais linhas ou colunas do que o pedido, como a quebra por grupo em vez do grupo) do valor errado. É só diagnóstico: os dois contam como erro.
- Nas de ressalva, o SQL precisa ter considerado `is_organizacao_politica`.
- Nas de recusa, o certo é terminar sem resposta baseada em consulta.
- O custo usa a tabela de preços informada na execução; o valor cobrado é o da fatura do provedor.
