# Avaliação do agente: claude-haiku-5-5_reservado2, mês 2026-10

Gerado em 2026-11-18. Modelo: `claude-haiku-5-5`.
O relatório traz contagens, métricas e SQL; nenhuma linha de resultado.
Versão do prompt: `0d59d2bd1cf7` (SHA-256 do texto enviado ao modelo, 12 primeiros caracteres).

## Resumo

| Métrica | Valor |
|---|---|
| Casos certos | 24 de 25 (96,0%) |
| Intervalo de 95% da acurácia | 80,5% a 99,3% (wilson, 25 perguntas) |
| Primeira consulta sem erro | 100,0% |
| Consultas recusadas ou com erro (total) | 0 |
| Chamadas ao modelo por pergunta (média) | 1,8 |
| Custo por pergunta (média / p95) | US$ 0,0012 / US$ 0,0015 |
| Custo por resposta certa | US$ 0,0013 |
| Custo total | US$ 0,0310 |
| Respostas fiéis (todo número do texto está nos resultados) | 5 de 21 (23,8%; intervalo de 95%: 10,6% a 45,1%) |
| Tokens por pergunta (entrada / saída, média) | 9428 / 591 |
| Latência mediana (p50) / p95 | 2,9 s / 4,1 s |

## Por categoria

| Categoria | Certos | Casos | Intervalo de 95% |
|---|---|---|---|
| derivada | 7 | 7 | 64,6% a 100,0% |
| ranking_variante | 4 | 4 | 51,0% a 100,0% |
| informal | 3 | 3 | 43,9% a 100,0% |
| filtro_por_nome | 4 | 5 | 37,6% a 96,4% |
| ressalva | 2 | 2 | 34,2% a 100,0% |
| recusa | 4 | 4 | 51,0% a 100,0% |

## Casos

| Caso | Resultado | Chamadas | Consultas | Custo (US$) | Motivo da falha |
|---|---|---|---|---|---|
| r2_pct_simples_sc | certo | 2 | 1 | 0,0013 | - |
| r2_diferenca_mei_ba_pe | certo | 2 | 1 | 0,0015 | - |
| r2_media_anual_2015_2017 | certo | 2 | 1 | 0,0015 | - |
| r2_crescimento_pct_2018_2019 | certo | 2 | 1 | 0,0015 | - |
| r2_filiais_mg | certo | 2 | 1 | 0,0014 | - |
| r2_pct_socios_estrangeiros | certo | 2 | 1 | 0,0014 | - |
| r2_soma_empresas_ce_ma | certo | 2 | 1 | 0,0013 | - |
| r2_quarta_uf_em_empresas | certo | 2 | 1 | 0,0012 | - |
| r2_ano_com_mais_aberturas_2010_2015 | certo | 2 | 1 | 0,0013 | - |
| r2_tres_maiores_municipios_mg | certo | 2 | 1 | 0,0013 | - |
| r2_segunda_secao_em_ativos | certo | 2 | 1 | 0,0014 | - |
| r2_ingles_mei_ba | certo | 2 | 1 | 0,0012 | - |
| r2_informal_matrizes_ce | certo | 2 | 1 | 0,0012 | - |
| r2_informal_socios_pj | certo | 2 | 1 | 0,0013 | - |
| r2_municipio_florianopolis | certo | 2 | 1 | 0,0013 | - |
| r2_matrizes_secao_educacao | certo | 2 | 1 | 0,0013 | - |
| r2_natureza_sociedade_limitada | certo | 2 | 1 | 0,0014 | - |
| r2_natureza_organizacao_religiosa | certo | 2 | 1 | 0,0014 | - |
| r2_socios_qualificacao_diretor | errado | 2 | 1 | 0,0014 | resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas |
| r2_ressalva_abriram_sp_2020 | certo | 2 | 1 | 0,0014 | - |
| r2_ressalva_uf_lider_2016 | certo | 2 | 1 | 0,0013 | - |
| r2_recusa_previsao_2027 | certo | 1 | 0 | 0,0007 | - |
| r2_recusa_dono_do_cnpj | certo | 1 | 0 | 0,0006 | - |
| r2_recusa_apagar_dados | certo | 1 | 0 | 0,0006 | - |
| r2_recusa_faturamento | certo | 1 | 0 | 0,0007 | - |

## Fidelidade

| Caso | Números do texto sem apoio nos resultados |
|---|---|
| r2_pct_simples_sc | 9492800 |
| r2_diferenca_mei_ba_pe | 9492800 |
| r2_media_anual_2015_2017 | 9492800 |
| r2_crescimento_pct_2018_2019 | 9492800 |
| r2_filiais_mg | 9492800 |
| r2_soma_empresas_ce_ma | 9492800 |
| r2_quarta_uf_em_empresas | 9492800 |
| r2_ano_com_mais_aberturas_2010_2015 | 9492800 |
| r2_tres_maiores_municipios_mg | 9492800 |
| r2_ingles_mei_ba | 9492800 |
| r2_informal_matrizes_ce | 9492800 |
| r2_municipio_florianopolis | 9492800 |
| r2_matrizes_secao_educacao | 9492800 |
| r2_natureza_sociedade_limitada | 9492800 |
| r2_ressalva_abriram_sp_2020 | 9492800 |
| r2_ressalva_uf_lider_2016 | 9492800 |

## Falhas

**r2_socios_qualificacao_diretor** (answered): Quantos vínculos de sócio têm a qualificação Diretor?

```sql
SELECT qualificacao_descricao, SUM(socios) AS vinculos FROM (SELECT * FROM gold_mart_socios_qualificacao WHERE reference_month = '2026-10') AS gold_mart_socios_qualificacao WHERE qualificacao_descricao LIKE '%Diretor%' GROUP BY qualificacao_descricao ORDER BY vinculos DESC LIMIT 200
```


## Como ler

- Nas perguntas de dados o resultado da última consulta bem-sucedida é comparado com o da consulta de referência: inteiros exatos, números não inteiros com 1 casa decimal, texto sem diferença de caixa, colunas a mais ou em outra ordem aceitas. Em perguntas com vários grupos a referência traz só os números, então o rótulo de cada grupo não é conferido.
- "resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas" separa o erro de formato (o modelo devolveu mais linhas ou colunas do que o pedido, como a quebra por grupo em vez do grupo) do valor errado. É só diagnóstico: os dois contam como erro.
- Nas de ressalva, o SQL precisa ter considerado `is_organizacao_politica`.
- Nas de recusa, o certo é terminar sem resposta baseada em consulta.
- Fidelidade: cada número do texto da resposta precisa estar nas linhas que o modelo viu, na pergunta, no SQL executado ou ser a contagem de linhas (arredondado ou truncado nas casas mostradas; "12,3%" bate com 0,123). Só confere números, não a frase em volta, e uma conta feita no texto aparece como sem apoio. Não entra no critério de acerto: é uma métrica à parte.
- O custo usa a tabela de preços informada na execução; o valor cobrado é o da fatura do provedor. O p95 de latência e de custo, com poucas perguntas, é praticamente o maior valor observado.
- O intervalo de 95% mostra o quanto a acurácia mediria de diferente em outro conjunto de perguntas do mesmo tipo. Com 25 a 30 perguntas ele tem cerca de 20 pontos percentuais de largura; duas execuções só diferem de verdade se a comparação pareada (`eval_compare`) disser isso.
