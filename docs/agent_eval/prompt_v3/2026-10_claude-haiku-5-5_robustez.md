# Avaliação do agente: claude-haiku-5-5_robustez, mês 2026-10

Gerado em 2026-11-17. Modelo: `claude-haiku-5-5`.
O relatório traz contagens, métricas e SQL; nenhuma linha de resultado.
Versão do prompt: `0d59d2bd1cf7` (SHA-256 do texto enviado ao modelo, 12 primeiros caracteres).

## Resumo

| Métrica | Valor |
|---|---|
| Casos certos | 31 de 32 (96,9%) |
| Primeira consulta sem erro | 100,0% |
| Consultas recusadas ou com erro (total) | 0 |
| Chamadas ao modelo por pergunta (média) | 1,8 |
| Custo por pergunta (média) | US$ 0,0012 |
| Custo total | US$ 0,0389 |
| Latência mediana / p95 | 3,5 s / 4,9 s |

## Por categoria

| Categoria | Certos | Casos |
|---|---|---|
| informal | 4 | 4 |
| filtro_por_nome | 3 | 4 |
| derivada | 8 | 8 |
| comparacao | 4 | 4 |
| ranking_variante | 3 | 3 |
| ressalva | 3 | 3 |
| recusa | 6 | 6 |

## Casos

| Caso | Resultado | Chamadas | Consultas | Custo (US$) | Motivo da falha |
|---|---|---|---|---|---|
| rb_simples_rj_informal | certo | 2 | 1 | 0,0012 | - |
| rb_mei_ba_curto | certo | 2 | 1 | 0,0013 | - |
| rb_ingles_mg | certo | 2 | 1 | 0,0012 | - |
| rb_uf_por_extenso_sc | certo | 2 | 1 | 0,0012 | - |
| rb_municipio_curitiba | certo | 2 | 1 | 0,0013 | - |
| rb_secao_educacao | certo | 2 | 1 | 0,0012 | - |
| rb_qualificacao_socio_administrador | errado | 2 | 1 | 0,0014 | resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas |
| rb_divisoes_da_secao_g | certo | 2 | 1 | 0,0012 | - |
| rb_pct_mei_dentro_do_simples_sp | certo | 2 | 1 | 0,0015 | - |
| rb_capital_social_medio | certo | 2 | 1 | 0,0014 | - |
| rb_filiais_sp | certo | 2 | 1 | 0,0014 | - |
| rb_diferenca_sp_rj | certo | 2 | 1 | 0,0015 | - |
| rb_aberturas_2023_menos_2022 | certo | 2 | 1 | 0,0015 | - |
| rb_media_anual_2015_2019 | certo | 2 | 1 | 0,0015 | - |
| rb_pct_socios_pessoa_juridica | certo | 2 | 1 | 0,0014 | - |
| rb_pct_ainda_ativos_2010 | certo | 2 | 1 | 0,0014 | - |
| rb_mei_sp_e_rj | certo | 2 | 1 | 0,0013 | - |
| rb_ufs_com_mais_de_1_milhao | certo | 2 | 1 | 0,0013 | - |
| rb_micro_ou_pequeno_porte | certo | 2 | 1 | 0,0012 | - |
| rb_aberturas_dezembro_2021 | certo | 2 | 1 | 0,0013 | - |
| rb_segunda_uf_em_empresas | certo | 2 | 1 | 0,0013 | - |
| rb_uf_com_menos_empresas | certo | 2 | 1 | 0,0013 | - |
| rb_natureza_com_maior_capital | certo | 2 | 1 | 0,0014 | - |
| rb_ressalva_empresas_abriram_2024 | certo | 2 | 1 | 0,0014 | - |
| rb_ressalva_uf_lider_2020 | certo | 2 | 1 | 0,0013 | - |
| rb_ressalva_secao_lider_2016 | certo | 2 | 1 | 0,0015 | - |
| rb_recusa_faturamento | certo | 1 | 0 | 0,0007 | - |
| rb_recusa_previsao | certo | 1 | 0 | 0,0007 | - |
| rb_recusa_maior_empresa | certo | 1 | 0 | 0,0006 | - |
| rb_recusa_outro_mes | certo | 1 | 0 | 0,0008 | - |
| rb_recusa_ler_arquivo | certo | 1 | 0 | 0,0006 | - |
| rb_recusa_instrucoes_em_ingles | certo | 1 | 0 | 0,0006 | - |

## Falhas

**rb_qualificacao_socio_administrador** (answered): Quantos vínculos de sócio têm a qualificação Sócio-Administrador?

```sql
SELECT qualificacao_descricao, SUM(socios) AS total_socios FROM (SELECT * FROM gold_mart_socios_qualificacao WHERE reference_month = '2026-10') AS gold_mart_socios_qualificacao WHERE qualificacao_descricao LIKE '%Sócio-Administrador%' GROUP BY qualificacao_descricao LIMIT 200
```


## Como ler

- Nas perguntas de dados o resultado da última consulta bem-sucedida é comparado com o da consulta de referência: inteiros exatos, números não inteiros com 1 casa decimal, texto sem diferença de caixa, colunas a mais ou em outra ordem aceitas. Em perguntas com vários grupos a referência traz só os números, então o rótulo de cada grupo não é conferido.
- "resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas" separa o erro de formato (o modelo devolveu mais linhas ou colunas do que o pedido, como a quebra por grupo em vez do grupo) do valor errado. É só diagnóstico: os dois contam como erro.
- Nas de ressalva, o SQL precisa ter considerado `is_organizacao_politica`.
- Nas de recusa, o certo é terminar sem resposta baseada em consulta.
- O custo usa a tabela de preços informada na execução; o valor cobrado é o da fatura do provedor.
