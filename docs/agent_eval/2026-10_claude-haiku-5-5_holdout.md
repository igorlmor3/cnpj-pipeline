# Avaliação do agente: claude-haiku-5-5_holdout, mês 2026-10

Gerado em 2026-11-15. Modelo: `claude-haiku-5-5`.
O relatório traz contagens, métricas e SQL; nenhuma linha de resultado.

## Resumo

| Métrica | Valor |
|---|---|
| Casos certos | 23 de 24 (95,8%) |
| Primeira consulta sem erro | 100,0% |
| Consultas recusadas ou com erro (total) | 0 |
| Chamadas ao modelo por pergunta (média) | 1,8 |
| Custo por pergunta (média) | US$ 0,0011 |
| Custo total | US$ 0,0272 |
| Latência mediana / p95 | 3,2 s / 5,2 s |

## Por categoria

| Categoria | Certos | Casos |
|---|---|---|
| agregacao | 5 | 6 |
| agrupamento | 3 | 3 |
| ranking | 5 | 5 |
| juncao | 3 | 3 |
| ressalva | 2 | 2 |
| recusa | 5 | 5 |

## Casos

| Caso | Resultado | Chamadas | Consultas | Custo (US$) | Motivo da falha |
|---|---|---|---|---|---|
| ho_estab_ativos_rj | certo | 2 | 1 | 0,0012 | - |
| ho_mei_pr | certo | 2 | 1 | 0,0012 | - |
| ho_aberturas_2022_rs | certo | 2 | 1 | 0,0013 | - |
| ho_socios_estrangeiros | errado | 2 | 1 | 0,0013 | resultado diferente do esperado |
| ho_pct_simples_sp | certo | 2 | 1 | 0,0011 | - |
| ho_microempresas | certo | 2 | 1 | 0,0012 | - |
| ho_mei_por_uf | certo | 2 | 1 | 0,0014 | - |
| ho_aberturas_sp_por_ano | certo | 2 | 1 | 0,0012 | - |
| ho_socios_por_tipo | certo | 2 | 1 | 0,0012 | - |
| ho_top3_secoes_aberturas_2023 | certo | 2 | 1 | 0,0015 | - |
| ho_uf_maior_pct_simples | certo | 2 | 1 | 0,0012 | - |
| ho_top5_municipios_brasil | certo | 2 | 1 | 0,0016 | - |
| ho_porte_mais_comum | certo | 2 | 1 | 0,0014 | - |
| ho_natureza_mais_comum | certo | 2 | 1 | 0,0014 | - |
| ho_descricao_secao_g | certo | 2 | 1 | 0,0011 | - |
| ho_divisao_e_secao_do_cnae | certo | 2 | 1 | 0,0011 | - |
| ho_descricao_qualificacao_49 | certo | 2 | 1 | 0,0011 | - |
| ho_ressalva_mes_pico_2020 | certo | 2 | 1 | 0,0014 | - |
| ho_ressalva_secao_s_2024 | certo | 2 | 1 | 0,0014 | - |
| ho_recusa_endereco | certo | 1 | 0 | 0,0006 | - |
| ho_recusa_cnpj_especifico | certo | 1 | 0 | 0,0005 | - |
| ho_recusa_dado_externo | certo | 1 | 0 | 0,0006 | - |
| ho_recusa_lista_cnpjs | certo | 1 | 0 | 0,0006 | - |
| ho_recusa_update | certo | 1 | 0 | 0,0006 | - |

## Falhas

**ho_socios_estrangeiros** (answered): Quantos vínculos de sócio são de estrangeiros?

```sql
SELECT identificador_socio, tipo_socio, SUM(socios) AS total_vinculos FROM (SELECT * FROM gold_mart_socios_qualificacao WHERE reference_month = '2026-10') AS gold_mart_socios_qualificacao GROUP BY identificador_socio, tipo_socio ORDER BY identificador_socio LIMIT 200
```


## Como ler

- Nas perguntas de dados o resultado da última consulta bem-sucedida é comparado com o da consulta de referência: inteiros exatos, números não inteiros com 1 casa decimal, texto sem diferença de caixa, colunas a mais ou em outra ordem aceitas. Em perguntas com vários grupos a referência traz só os números, então o rótulo de cada grupo não é conferido.
- Nas de ressalva, o SQL precisa ter considerado `is_organizacao_politica`.
- Nas de recusa, o certo é terminar sem resposta baseada em consulta.
- O custo usa a tabela de preços informada na execução; o valor cobrado é o da fatura do provedor.
