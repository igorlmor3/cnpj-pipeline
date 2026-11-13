# Catálogo de regras de qualidade de dados

Este documento registra cada problema de qualidade encontrado nos dados do CNPJ e a decisão tomada. Ele é preenchido durante o profiling da camada bronze. A seção de hipóteses lista pontos a verificar e não conta como resultado até ser confirmada com dados reais.

## Como usar

1. Rode o profiling: `python -m cnpj_pipeline.profiling --month AAAA-MM`. Os relatórios ficam em `docs/profiling/<mês>/`. Cada achado automático é um candidato e exige decisão humana.
2. Para cada problema confirmado, crie uma linha na tabela de regras com um identificador (`DQ-001`, `DQ-002`, ...).
3. Registre a decisão, a camada em que a regra é aplicada e o teste que a cobre.
4. Registre quantas linhas foram afetadas na última execução.

## Regras confirmadas

| ID | Tabela | Coluna | Problema observado | Decisão | Camada | Teste | Linhas afetadas |
| --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | |

Decisões possíveis: `corrigir` (transformação determinística), `anular` (valor vira nulo), `rejeitar` (linha vai para a quarentena), `manter` (documentado, sem ação).

## Decisões já aplicadas na camada bronze

- Campos vazios são gravados como NULL, porque o CSV não distingue vazio de ausente.
- Linhas com número de colunas diferente do esquema ou com aspas inválidas são rejeitadas e gravadas em `data/bronze/_rejects/`, nunca descartadas em silêncio.
- Se o número de colunas do arquivo não bate com `schemas.py`, o processamento para.

## Regras provisórias da camada silver

Implementadas em `src/cnpj_pipeline/silver_rules.py` e cobertas por `tests/test_silver.py`. Todas partem de hipóteses e precisam ser confirmadas contra o profiling do mês real antes de entrarem na tabela de regras confirmadas.

- Texto: caracteres de controle viram espaço, espaços repetidos são juntados, bordas aparadas, vazio vira NULL.
- Códigos (natureza, qualificação, CNAE, município, país, CEP, DDD, telefone): apenas aparados, para preservar zeros à esquerda.
- Datas `AAAAMMDD`: formato inválido, zeros e datas inexistentes viram NULL.
- Decimais: vírgula ou ponto aceitos; valor inválido vira NULL.
- Indicadores S/N: qualquer outro valor vira NULL.
- UF: fora das 27 siglas e `EX` vira NULL.
- Chaves (`cnpj_basico`, `cnpj_ordem`, `cnpj_dv`, `codigo`): formato inválido envia a linha para a quarentena.
- Chaves duplicadas: uma linha é mantida (hash do conteúdo) e as demais vão para a quarentena. Sócios não têm chave única e não são deduplicados.

## Hipóteses a verificar

Itens a confirmar contra o dicionário de layout do mês processado e contra os dados.

- Os arquivos não têm linha de cabeçalho; os nomes das colunas vêm do dicionário de layout.
- Separador `;`, aspas duplas como delimitador de texto e encoding `ISO-8859-1`.
- Campos monetários (por exemplo, capital social) usam vírgula como separador decimal.
- Datas vêm como texto `AAAAMMDD`; valores como `0` ou `00000000` podem representar ausência de data.
- O CNPJ alfanumérico exige tratar `cnpj_basico`, `cnpj_ordem` e `cnpj_dv` como texto.
- A estrutura de colunas do arquivo de sócios mudou em 2026; validar contra o dicionário do mês.
- Possíveis chaves órfãs entre Estabelecimentos e Empresas, e entre Estabelecimentos e as tabelas de domínio (CNAE, município, motivo).
- Possíveis duplicidades por chave de negócio.
- Campos de texto com espaços extras, caracteres de controle ou problemas de codificação.
- Campos de sócios podem vir mascarados (por exemplo, CPF parcialmente ocultado); confirmar no dicionário.
