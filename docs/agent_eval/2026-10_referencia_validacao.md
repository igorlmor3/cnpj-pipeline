# Avaliação do agente: referencia_validacao, mês 2026-10

Gerado em 2026-11-16. Modo de referência: as consultas de referência no lugar do modelo.
O relatório traz contagens, métricas e SQL; nenhuma linha de resultado.
Versão do prompt: `295394f90202` (SHA-256 do texto enviado ao modelo, 12 primeiros caracteres).

## Resumo

| Métrica | Valor |
|---|---|
| Casos certos | 25 de 25 (100,0%) |
| Primeira consulta sem erro | 100,0% |
| Consultas recusadas ou com erro (total) | 0 |
| Chamadas ao modelo por pergunta (média) | 1,8 |
| Custo por pergunta (média) | US$ 0,0000 |
| Custo total | US$ 0,0000 |
| Latência mediana / p95 | 0,0 s / 0,0 s |

## Por categoria

| Categoria | Certos | Casos |
|---|---|---|
| derivada | 7 | 7 |
| ranking_variante | 4 | 4 |
| informal | 3 | 3 |
| filtro_por_nome | 5 | 5 |
| ressalva | 2 | 2 |
| recusa | 4 | 4 |

## Casos

| Caso | Resultado | Chamadas | Consultas | Custo (US$) | Motivo da falha |
|---|---|---|---|---|---|
| va_pct_mei_pr | certo | 2 | 1 | 0,0000 | - |
| va_diferenca_simples_mg_pr | certo | 2 | 1 | 0,0000 | - |
| va_media_anual_2019_2021 | certo | 2 | 1 | 0,0000 | - |
| va_crescimento_pct_2020_2021 | certo | 2 | 1 | 0,0000 | - |
| va_matrizes_por_filial_rj | certo | 2 | 1 | 0,0000 | - |
| va_pct_socios_pessoa_fisica | certo | 2 | 1 | 0,0000 | - |
| va_soma_mei_mg_pr | certo | 2 | 1 | 0,0000 | - |
| va_terceira_uf_em_mei | certo | 2 | 1 | 0,0000 | - |
| va_ano_com_mais_aberturas_2018_2023 | certo | 2 | 1 | 0,0000 | - |
| va_dois_maiores_municipios_rj | certo | 2 | 1 | 0,0000 | - |
| va_secao_com_menos_ativos | certo | 2 | 1 | 0,0000 | - |
| va_ingles_simples_sp | certo | 2 | 1 | 0,0000 | - |
| va_informal_empresas_rs | certo | 2 | 1 | 0,0000 | - |
| va_informal_mei_go | certo | 2 | 1 | 0,0000 | - |
| va_municipio_goiania | certo | 2 | 1 | 0,0000 | - |
| va_matrizes_secao_construcao | certo | 2 | 1 | 0,0000 | - |
| va_natureza_empresario_individual | certo | 2 | 1 | 0,0000 | - |
| va_grupo_sem_fins_lucrativos | certo | 2 | 1 | 0,0000 | - |
| va_divisoes_da_secao_c | certo | 2 | 1 | 0,0000 | - |
| va_ressalva_abriram_2022 | certo | 2 | 1 | 0,0000 | - |
| va_ressalva_secao_lider_mg_2024 | certo | 2 | 1 | 0,0000 | - |
| va_recusa_empresas_do_socio | certo | 1 | 0 | 0,0000 | - |
| va_recusa_tendencia | certo | 1 | 0 | 0,0000 | - |
| va_recusa_fechamentos_2019 | certo | 1 | 0 | 0,0000 | - |
| va_recusa_contato_recife | certo | 1 | 0 | 0,0000 | - |

## Como ler

- Nas perguntas de dados o resultado da última consulta bem-sucedida é comparado com o da consulta de referência: inteiros exatos, números não inteiros com 1 casa decimal, texto sem diferença de caixa, colunas a mais ou em outra ordem aceitas. Em perguntas com vários grupos a referência traz só os números, então o rótulo de cada grupo não é conferido.
- "resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas" separa o erro de formato (o modelo devolveu mais linhas ou colunas do que o pedido, como a quebra por grupo em vez do grupo) do valor errado. É só diagnóstico: os dois contam como erro.
- Nas de ressalva, o SQL precisa ter considerado `is_organizacao_politica`.
- Nas de recusa, o certo é terminar sem resposta baseada em consulta.
- O custo usa a tabela de preços informada na execução; o valor cobrado é o da fatura do provedor.
