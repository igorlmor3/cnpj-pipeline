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
- O encoding `ISO-8859-1` é convertido para UTF-8 durante a extração do zip. Motivo: os arquivos reais de 2026-10 (Estabelecimentos0 e Estabelecimentos1) contêm bytes de controle C1 (`0x8F`) que o DuckDB recusa com "File is not latin-1 encoded". A bronze preserva esses bytes como os pontos de código equivalentes (U+0080 a U+009F) e registra a quantidade em `control_chars` no manifesto; a silver os troca por espaço (regra `text`).
- Também aparece o byte `0x1A` (SUB) no lugar de letras acentuadas perdidas na origem (por exemplo, `ASUNCI` seguido de SUB e `N`). A silver o trata como caractere de controle e o troca por espaço; a letra original não é recuperável.

## Regras provisórias da camada silver

Implementadas em `src/cnpj_pipeline/silver_rules.py` e cobertas por `tests/test_silver.py`. Todas partem de hipóteses e precisam ser confirmadas contra o profiling do mês real antes de entrarem na tabela de regras confirmadas.

- Texto: caracteres de controle viram espaço, espaços repetidos são juntados, bordas aparadas, vazio vira NULL.
- Códigos (natureza, qualificação, CNAE, município, país): apenas aparados, para preservar zeros à esquerda.
- Telefone, DDD, fax e CEP: aparados, e valor composto só de zeros vira NULL (profiling de 2026-10: `0`, `0000` e `00000000` aparecem em cerca de 3% de `ddd_1` e `telefone_1`).
- Texto de endereço e nome fantasia (`nome_fantasia`, `logradouro`, `numero`, `complemento`, `bairro`) e e-mail: valores só de zeros, só de pontos ou hífens, `N/A`, `NULL`, `NAO INFORMADO` e `SEM INFORMACAO` viram NULL. `S/N` em `numero` é mantido.
- Datas `AAAAMMDD`: formato inválido, zeros e datas inexistentes viram NULL.
- Decimais: vírgula ou ponto aceitos; valor inválido vira NULL.
- Indicadores S/N: qualquer outro valor vira NULL.
- UF: fora das 27 siglas e `EX` vira NULL.
- Chaves (`cnpj_basico`, `cnpj_ordem`, `cnpj_dv`, `codigo`): formato inválido envia a linha para a quarentena.
- Chaves duplicadas: uma linha é mantida (a mais preenchida depois da limpeza; no empate, o hash do conteúdo) e as demais vão para a quarentena. Sócios não têm chave única e não são deduplicados.

## Achados do mês real (2026-10)

- `cnpj_basico = 00000000` não é sentinela: é o Banco do Brasil. O mesmo vale para `cnpj_dv = 00` (dígito verificador válido) e `motivo_situacao_cadastral = 00` ("sem motivo", presente em todos os estabelecimentos ativos). O profiler os marca como candidatos a sentinela, e nenhum deve ser anulado.
- Empresas tem 1 chave `cnpj_basico` duplicada, no arquivo `Empresas2`, com conteúdo diferente em todas as demais colunas, exceto o ente federativo, vazio nas duas. Não é cópia exata. Uma das linhas é um registro praticamente vazio (sem razão social nem porte, natureza 0000 "não informada" e capital zero) e a outra é completa. A primeira versão da silver escolhia pelo hash do conteúdo e manteve a vazia, o que deixava 51 estabelecimentos (2 matrizes, 6 ativos) com natureza "não informada", porte vazio e capital zero na gold. A regra atual mantém a linha mais preenchida e usa o hash só para desempatar; a vazia vai para a quarentena como `duplicate_key`. O cadastro sozinho não mostra qual das duas é a empresa "verdadeira"; a regra assume que a linha com mais informação é a melhor.
- Estabelecimentos: com as regras de espaço reservado, a silver anulou, entre outros, 842 mil números (`0`, `000000`...), 2,7 milhões de `ddd_1` e 2,9 milhões de `telefone_1` só de zeros, e 286 mil bairros. Em um dos arquivos, o valor de bairro mais anulado é `NÃO INFORMADO` com acento, variante que a lista de sentinelas do profiling não detecta (ela reconhece só `NAO INFORMADO`).
- Sócios: `cnpj_cpf_socio` e `representante_legal` vêm mascarados pela própria Receita (`***999999**`). Para pessoa jurídica, `cnpj_cpf_socio` tem 8 dígitos.
- Sócios: `qualificacao_representante_legal = 00` (97%) e `faixa_etaria = 0` (2,6%) são códigos de "não se aplica", não erros.
- Simples: 49,6% das datas de exclusão do Simples e 60% das de exclusão do MEI vêm zeradas (`00000000`), o que significa que a empresa não foi excluída. Há datas de exclusão futuras (até 2026-12-31).
- Os CSVs trazem bytes de controle C1 (cinco em 223 milhões de linhas) e `0x1A` no lugar de letras acentuadas perdidas na origem.

## Verificações automatizadas (`quality.py`)

As verificações do módulo `quality.py` rodam depois das camadas e registram o resultado em `docs/quality/<mês>.md`. Primeiros achados no mês real (2026-10), todos informativos, sem ação na silver até haver decisão:

| Verificação | Linhas afetadas | Leitura |
| --- | ---: | --- |
| Dígito verificador do CNPJ | 0 de 73.809.379 | todos os CNPJs conferem pelo módulo 11 |
| Matriz com ordem diferente de 0001 | 11.574 | a hipótese de que matriz é sempre a ordem 0001 não vale para todos |
| Situação cadastral anterior ao início da atividade | 1.065 | datas incoerentes na origem |
| Início de atividade anterior a 1900 | 4 | datas improváveis na origem |
| Empresas sem porte | 4.113 | o porte vem vazio; os demais valores são só 01, 03 e 05 |
| Exclusão do Simples ou do MEI anterior à opção | 3 | diferença de um dia |
| Opção do Simples posterior ao fim do mês | 11 | todas com data 2027-01-01: opções agendadas, não erro |

Sem nenhuma violação: formato do CNPJ, identificador matriz/filial, códigos de situação cadastral, UF, formato do CNAE, início de atividade nulo ou futuro, natureza jurídica, capital social negativo, tipo de sócio e datas futuras de sócios; também as chaves únicas do Simples e dos códigos das tabelas de domínio. As 42 verificações de contagem entre camadas passaram: bronze, silver, quarentena e gold batem entre si. A execução completa (73 verificações: 66 ok, 7 com aviso, 0 falhas) inclui as chaves únicas de Empresas e Estabelecimentos, empresas sem matriz e sócios sem empresa, todas com 0 violações, e está registrada em `docs/quality/2026-10.md`.

## Hipóteses a verificar

Itens a confirmar contra o dicionário de layout do mês processado e contra os dados.

- Os arquivos não têm linha de cabeçalho; os nomes das colunas vêm do dicionário de layout.
- Separador `;`, aspas duplas como delimitador de texto e encoding `ISO-8859-1`.
- Campos monetários (por exemplo, capital social) usam vírgula como separador decimal.
- Datas vêm como texto `AAAAMMDD`; valores como `0` ou `00000000` podem representar ausência de data.
- O CNPJ alfanumérico exige tratar `cnpj_basico`, `cnpj_ordem` e `cnpj_dv` como texto.
- A estrutura de colunas do arquivo de sócios mudou em 2026; as 11 colunas de `schemas.py` foram validadas contra os dados de 2026-10 (nenhuma linha rejeitada), mas o dicionário oficial do mês ainda deve ser conferido.
- Possíveis chaves órfãs entre Estabelecimentos e Empresas, e entre Estabelecimentos e as tabelas de domínio (CNAE, município, motivo).
- Possíveis duplicidades por chave de negócio.
- Campos de texto com espaços extras, caracteres de controle ou problemas de codificação.
- Campos de sócios podem vir mascarados (por exemplo, CPF parcialmente ocultado); confirmar no dicionário.
