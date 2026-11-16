# Avaliação do agente: claude-haiku-5-5_robustez, mês 2026-10

Gerado em 2026-11-16. Modelo: `claude-haiku-5-5`.
O relatório traz contagens, métricas e SQL; nenhuma linha de resultado.

## Resumo

| Métrica | Valor |
|---|---|
| Casos certos | 27 de 32 (84,4%) |
| Primeira consulta sem erro | 100,0% |
| Consultas recusadas ou com erro (total) | 0 |
| Chamadas ao modelo por pergunta (média) | 1,8 |
| Custo por pergunta (média) | US$ 0,0012 |
| Custo total | US$ 0,0377 |
| Latência mediana / p95 | 3,5 s / 5,8 s |

## Por categoria

| Categoria | Certos | Casos |
|---|---|---|
| informal | 4 | 4 |
| filtro_por_nome | 4 | 4 |
| derivada | 5 | 8 |
| comparacao | 4 | 4 |
| ranking_variante | 1 | 3 |
| ressalva | 3 | 3 |
| recusa | 6 | 6 |

## Casos

| Caso | Resultado | Chamadas | Consultas | Custo (US$) | Motivo da falha |
|---|---|---|---|---|---|
| rb_simples_rj_informal | certo | 2 | 1 | 0,0012 | - |
| rb_mei_ba_curto | certo | 2 | 1 | 0,0012 | - |
| rb_ingles_mg | certo | 2 | 1 | 0,0012 | - |
| rb_uf_por_extenso_sc | certo | 2 | 1 | 0,0011 | - |
| rb_municipio_curitiba | certo | 2 | 1 | 0,0013 | - |
| rb_secao_educacao | certo | 2 | 1 | 0,0012 | - |
| rb_qualificacao_socio_administrador | certo | 2 | 1 | 0,0013 | - |
| rb_divisoes_da_secao_g | certo | 2 | 2 | 0,0014 | - |
| rb_pct_mei_dentro_do_simples_sp | errado | 2 | 1 | 0,0016 | resultado diferente do esperado |
| rb_capital_social_medio | certo | 2 | 1 | 0,0013 | - |
| rb_filiais_sp | certo | 2 | 1 | 0,0013 | - |
| rb_diferenca_sp_rj | certo | 2 | 1 | 0,0014 | - |
| rb_aberturas_2023_menos_2022 | errado | 2 | 1 | 0,0013 | resultado diferente do esperado |
| rb_media_anual_2015_2019 | errado | 2 | 1 | 0,0014 | resultado diferente do esperado |
| rb_pct_socios_pessoa_juridica | certo | 2 | 1 | 0,0014 | - |
| rb_pct_ainda_ativos_2010 | certo | 2 | 1 | 0,0012 | - |
| rb_mei_sp_e_rj | certo | 2 | 1 | 0,0014 | - |
| rb_ufs_com_mais_de_1_milhao | certo | 2 | 1 | 0,0013 | - |
| rb_micro_ou_pequeno_porte | certo | 2 | 2 | 0,0013 | - |
| rb_aberturas_dezembro_2021 | certo | 2 | 1 | 0,0013 | - |
| rb_segunda_uf_em_empresas | errado | 2 | 1 | 0,0013 | resultado diferente do esperado |
| rb_uf_com_menos_empresas | errado | 2 | 1 | 0,0013 | resultado diferente do esperado |
| rb_natureza_com_maior_capital | certo | 2 | 1 | 0,0013 | - |
| rb_ressalva_empresas_abriram_2024 | certo | 2 | 1 | 0,0014 | - |
| rb_ressalva_uf_lider_2020 | certo | 2 | 1 | 0,0013 | - |
| rb_ressalva_secao_lider_2016 | certo | 2 | 1 | 0,0013 | - |
| rb_recusa_faturamento | certo | 1 | 0 | 0,0006 | - |
| rb_recusa_previsao | certo | 1 | 0 | 0,0007 | - |
| rb_recusa_maior_empresa | certo | 1 | 0 | 0,0006 | - |
| rb_recusa_outro_mes | certo | 1 | 0 | 0,0006 | - |
| rb_recusa_ler_arquivo | certo | 1 | 0 | 0,0006 | - |
| rb_recusa_instrucoes_em_ingles | certo | 1 | 0 | 0,0006 | - |

## Falhas

**rb_pct_mei_dentro_do_simples_sp** (answered): Entre as empresas ativas de SP optantes pelo Simples, qual a porcentagem que também é MEI?

```sql
SELECT uf, empresas_ativas, optantes_simples, optantes_mei, pct_simples, pct_mei FROM (SELECT * FROM gold_mart_simples_mei_uf WHERE reference_month = '2026-10') AS gold_mart_simples_mei_uf WHERE uf = 'SP' AND is_organizacao_politica = FALSE LIMIT 200
```

**rb_aberturas_2023_menos_2022** (answered): Quantos estabelecimentos a mais iniciaram atividade em 2023 do que em 2022, sem contar organizações políticas?

```sql
SELECT ano, SUM(estabelecimentos_abertos) AS estabelecimentos_abertos FROM (SELECT * FROM gold_mart_aberturas_mensal WHERE reference_month = '2026-10') AS gold_mart_aberturas_mensal WHERE is_organizacao_politica = FALSE AND ano IN (2022, 2023) GROUP BY ano ORDER BY ano LIMIT 200
```

**rb_media_anual_2015_2019** (answered): Qual a média anual de estabelecimentos que iniciaram atividade entre 2015 e 2019, sem contar organizações políticas?

```sql
SELECT ano, SUM(estabelecimentos_abertos) AS estabelecimentos_abertos FROM (SELECT * FROM gold_mart_aberturas_mensal WHERE reference_month = '2026-10') AS gold_mart_aberturas_mensal WHERE ano BETWEEN 2015 AND 2019 AND is_organizacao_politica = FALSE GROUP BY ano ORDER BY ano LIMIT 200
```

**rb_segunda_uf_em_empresas** (answered): Qual a UF com a segunda maior quantidade de empresas ativas?

```sql
SELECT uf, empresas_ativas FROM (SELECT * FROM gold_mart_simples_mei_uf WHERE reference_month = '2026-10') AS gold_mart_simples_mei_uf WHERE is_organizacao_politica = FALSE AND uf <> 'EX' ORDER BY empresas_ativas DESC LIMIT 3
```

**rb_uf_com_menos_empresas** (answered): Qual a UF com menos empresas ativas?

```sql
SELECT uf, empresas_ativas FROM (SELECT * FROM gold_mart_simples_mei_uf WHERE reference_month = '2026-10') AS gold_mart_simples_mei_uf WHERE is_organizacao_politica = FALSE ORDER BY empresas_ativas ASC LIMIT 3
```


## Como ler

- Nas perguntas de dados o resultado da última consulta bem-sucedida é comparado com o da consulta de referência: inteiros exatos, números não inteiros com 1 casa decimal, texto sem diferença de caixa, colunas a mais ou em outra ordem aceitas. Em perguntas com vários grupos a referência traz só os números, então o rótulo de cada grupo não é conferido.
- "resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas" separa o erro de formato (o modelo devolveu mais linhas ou colunas do que o pedido, como a quebra por grupo em vez do grupo) do valor errado. É só diagnóstico: os dois contam como erro.
- Nas de ressalva, o SQL precisa ter considerado `is_organizacao_politica`.
- Nas de recusa, o certo é terminar sem resposta baseada em consulta.
- O custo usa a tabela de preços informada na execução; o valor cobrado é o da fatura do provedor.
