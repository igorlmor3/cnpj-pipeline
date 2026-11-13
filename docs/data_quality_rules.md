# Catálogo de regras de qualidade de dados

Este documento registra cada problema de qualidade encontrado nos dados do CNPJ e a decisão tomada. Ele é preenchido durante o profiling da camada bronze. A seção de hipóteses lista pontos a verificar e não conta como resultado até ser confirmada com dados reais.

## Como usar

1. Rode o profiling da tabela (contagem de nulos, valores distintos, mínimos e máximos, padrões de formato).
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
