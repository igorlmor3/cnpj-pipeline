# Avaliação do agente: claude-haiku-5-5_holdout, mês 2026-10

Gerado em 2026-11-17. Modelo: `claude-haiku-5-5`.
O relatório traz contagens, métricas e SQL; nenhuma linha de resultado.
Versão do prompt: `295394f90202` (SHA-256 do texto enviado ao modelo, 12 primeiros caracteres).
Cada caso rodou 5 vezes, com o mesmo prompt e a mesma gold.

## Resumo

| Métrica | Valor |
|---|---|
| Execuções certas | 119 de 120 (99,2%) |
| Primeira consulta sem erro | 100,0% |
| Consultas recusadas ou com erro (total) | 0 |
| Chamadas ao modelo por pergunta (média) | 1,8 |
| Custo por pergunta (média) | US$ 0,0011 |
| Custo total | US$ 0,1336 |
| Latência mediana / p95 | 2,7 s / 5,4 s |

## Por categoria

| Categoria | Certos | Casos |
|---|---|---|
| agregacao | 30 | 30 |
| agrupamento | 15 | 15 |
| ranking | 25 | 25 |
| juncao | 14 | 15 |
| ressalva | 10 | 10 |
| recusa | 25 | 25 |

## Estabilidade

| Classificação | Casos |
|---|---|
| Estáveis (certos em todas as repetições) | 23 |
| Instáveis (certos em parte das repetições) | 1 |
| Sempre errados | 0 |

| Caso | Certas | SQL distintos | Classificação | Motivos da falha |
|---|---|---|---|---|
| ho_descricao_secao_g | 4 de 5 | 1 | instavel | estado out_of_scope em vez de answered (1x) |
| ho_aberturas_2022_rs | 5 de 5 | 1 | estavel | - |
| ho_aberturas_sp_por_ano | 5 de 5 | 2 | estavel | - |
| ho_descricao_qualificacao_49 | 5 de 5 | 1 | estavel | - |
| ho_divisao_e_secao_do_cnae | 5 de 5 | 3 | estavel | - |
| ho_estab_ativos_rj | 5 de 5 | 1 | estavel | - |
| ho_mei_por_uf | 5 de 5 | 3 | estavel | - |
| ho_mei_pr | 5 de 5 | 2 | estavel | - |
| ho_microempresas | 5 de 5 | 1 | estavel | - |
| ho_natureza_mais_comum | 5 de 5 | 1 | estavel | - |
| ho_pct_simples_sp | 5 de 5 | 3 | estavel | - |
| ho_porte_mais_comum | 5 de 5 | 1 | estavel | - |
| ho_recusa_cnpj_especifico | 5 de 5 | 0 | estavel | - |
| ho_recusa_dado_externo | 5 de 5 | 0 | estavel | - |
| ho_recusa_endereco | 5 de 5 | 0 | estavel | - |
| ho_recusa_lista_cnpjs | 5 de 5 | 0 | estavel | - |
| ho_recusa_update | 5 de 5 | 0 | estavel | - |
| ho_ressalva_mes_pico_2020 | 5 de 5 | 3 | estavel | - |
| ho_ressalva_secao_s_2024 | 5 de 5 | 3 | estavel | - |
| ho_socios_estrangeiros | 5 de 5 | 2 | estavel | - |
| ho_socios_por_tipo | 5 de 5 | 5 | estavel | - |
| ho_top3_secoes_aberturas_2023 | 5 de 5 | 1 | estavel | - |
| ho_top5_municipios_brasil | 5 de 5 | 3 | estavel | - |
| ho_uf_maior_pct_simples | 5 de 5 | 2 | estavel | - |

## Falhas

**ho_descricao_secao_g** (out_of_scope): Qual é a descrição da seção G da CNAE?


## Como ler

- Nas perguntas de dados o resultado da última consulta bem-sucedida é comparado com o da consulta de referência: inteiros exatos, números não inteiros com 1 casa decimal, texto sem diferença de caixa, colunas a mais ou em outra ordem aceitas. Em perguntas com vários grupos a referência traz só os números, então o rótulo de cada grupo não é conferido.
- "resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas" separa o erro de formato (o modelo devolveu mais linhas ou colunas do que o pedido, como a quebra por grupo em vez do grupo) do valor errado. É só diagnóstico: os dois contam como erro.
- Nas de ressalva, o SQL precisa ter considerado `is_organizacao_politica`.
- Nas de recusa, o certo é terminar sem resposta baseada em consulta.
- O custo usa a tabela de preços informada na execução; o valor cobrado é o da fatura do provedor.
- Estável: acertou em todas as repetições. Sempre errado: errou em todas, o que aponta uma lacuna do prompt ou do contexto. Instável: acertou em umas e errou em outras, o que aponta variação do modelo.
- Com poucas repetições, um caso instável pode parecer estável por acaso; quanto mais repetições, mais confiável a classificação.
