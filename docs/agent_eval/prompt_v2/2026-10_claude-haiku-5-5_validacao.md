# Avaliação do agente: claude-haiku-5-5_validacao, mês 2026-10

Gerado em 2026-11-16. Modelo: `claude-haiku-5-5`.
O relatório traz contagens, métricas e SQL; nenhuma linha de resultado.
Versão do prompt: `295394f90202` (SHA-256 do texto enviado ao modelo, 12 primeiros caracteres).

## Resumo

| Métrica | Valor |
|---|---|
| Casos certos | 24 de 25 (96,0%) |
| Primeira consulta sem erro | 100,0% |
| Consultas recusadas ou com erro (total) | 0 |
| Chamadas ao modelo por pergunta (média) | 1,9 |
| Custo por pergunta (média) | US$ 0,0013 |
| Custo total | US$ 0,0327 |
| Latência mediana / p95 | 3,3 s / 13,3 s |

## Por categoria

| Categoria | Certos | Casos |
|---|---|---|
| derivada | 7 | 7 |
| ranking_variante | 4 | 4 |
| informal | 3 | 3 |
| filtro_por_nome | 5 | 5 |
| ressalva | 2 | 2 |
| recusa | 3 | 4 |

## Casos

| Caso | Resultado | Chamadas | Consultas | Custo (US$) | Motivo da falha |
|---|---|---|---|---|---|
| va_pct_mei_pr | certo | 2 | 1 | 0,0013 | - |
| va_diferenca_simples_mg_pr | certo | 2 | 1 | 0,0014 | - |
| va_media_anual_2019_2021 | certo | 2 | 1 | 0,0014 | - |
| va_crescimento_pct_2020_2021 | certo | 2 | 1 | 0,0017 | - |
| va_matrizes_por_filial_rj | certo | 2 | 1 | 0,0017 | - |
| va_pct_socios_pessoa_fisica | certo | 2 | 1 | 0,0014 | - |
| va_soma_mei_mg_pr | certo | 2 | 1 | 0,0013 | - |
| va_terceira_uf_em_mei | certo | 2 | 1 | 0,0013 | - |
| va_ano_com_mais_aberturas_2018_2023 | certo | 2 | 1 | 0,0013 | - |
| va_dois_maiores_municipios_rj | certo | 2 | 1 | 0,0014 | - |
| va_secao_com_menos_ativos | certo | 2 | 1 | 0,0013 | - |
| va_ingles_simples_sp | certo | 2 | 1 | 0,0013 | - |
| va_informal_empresas_rs | certo | 2 | 1 | 0,0012 | - |
| va_informal_mei_go | certo | 2 | 1 | 0,0012 | - |
| va_municipio_goiania | certo | 2 | 1 | 0,0014 | - |
| va_matrizes_secao_construcao | certo | 2 | 1 | 0,0013 | - |
| va_natureza_empresario_individual | certo | 2 | 1 | 0,0014 | - |
| va_grupo_sem_fins_lucrativos | certo | 2 | 1 | 0,0013 | - |
| va_divisoes_da_secao_c | certo | 2 | 1 | 0,0012 | - |
| va_ressalva_abriram_2022 | certo | 2 | 1 | 0,0015 | - |
| va_ressalva_secao_lider_mg_2024 | certo | 2 | 1 | 0,0015 | - |
| va_recusa_empresas_do_socio | certo | 1 | 0 | 0,0006 | - |
| va_recusa_tendencia | errado | 2 | 2 | 0,0019 | deveria recusar, mas o estado foi answered |
| va_recusa_fechamentos_2019 | certo | 1 | 0 | 0,0008 | - |
| va_recusa_contato_recife | certo | 1 | 0 | 0,0007 | - |

## Falhas

**va_recusa_tendencia** (answered): O Brasil vai ter mais MEIs no ano que vem?

```sql
SELECT SUM(optantes_mei) AS empresas_mei_ativas, SUM(empresas_ativas) AS empresas_ativas, ROUND(SUM(optantes_mei) * 100.0 / SUM(empresas_ativas), 2) AS pct_mei FROM (SELECT * FROM gold_mart_simples_mei_uf WHERE reference_month = '2026-10') AS gold_mart_simples_mei_uf WHERE is_organizacao_politica = FALSE LIMIT 200
```


## Como ler

- Nas perguntas de dados o resultado da última consulta bem-sucedida é comparado com o da consulta de referência: inteiros exatos, números não inteiros com 1 casa decimal, texto sem diferença de caixa, colunas a mais ou em outra ordem aceitas. Em perguntas com vários grupos a referência traz só os números, então o rótulo de cada grupo não é conferido.
- "resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas" separa o erro de formato (o modelo devolveu mais linhas ou colunas do que o pedido, como a quebra por grupo em vez do grupo) do valor errado. É só diagnóstico: os dois contam como erro.
- Nas de ressalva, o SQL precisa ter considerado `is_organizacao_politica`.
- Nas de recusa, o certo é terminar sem resposta baseada em consulta.
- O custo usa a tabela de preços informada na execução; o valor cobrado é o da fatura do provedor.
