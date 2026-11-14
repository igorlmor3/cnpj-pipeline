# Profiling: Estabelecimentos (2026-10)

- Arquivos Parquet: 10
- Linhas analisadas: 3.689.011
- Amostra: 5.0% das linhas (resultados aproximados)
- Dados pessoais: este relatório contém apenas agregados e padrões mascarados

## Achados automáticos

- `cnpj_basico`: valor candidato a sentinela `00000000` em 400 linha(s) (0.0108%)
- `cnpj_dv`: valor candidato a sentinela `00` em 121746 linha(s) (3.3002%)
- `nome_fantasia`: 63.8559% de nulos
- `nome_fantasia`: valor candidato a sentinela `.` em 88 linha(s) (0.0024%)
- `nome_fantasia`: valor candidato a sentinela `-` em 75 linha(s) (0.002%)
- `nome_fantasia`: valor candidato a sentinela `0` em 6 linha(s) (0.0002%)
- `nome_fantasia`: valor candidato a sentinela `NAO INFORMADO` em 2 linha(s) (0.0001%)
- `nome_fantasia`: valor candidato a sentinela `00` em 2 linha(s) (0.0001%)
- `nome_fantasia`: valor candidato a sentinela `N/A` em 1 linha(s) (0.0%)
- `nome_fantasia`: valor candidato a sentinela `SEM INFORMACAO` em 1 linha(s) (0.0%)
- `nome_fantasia`: 1 valor(es) com espaços nas bordas
- `nome_fantasia`: 2 valor(es) com caracteres de controle
- `data_situacao_cadastral`: valor candidato a sentinela `0` em 5726 linha(s) (0.1552%)
- `motivo_situacao_cadastral`: valor candidato a sentinela `00` em 1418395 linha(s) (38.4492%)
- `nome_cidade_exterior`: 99.9429% de nulos
- `nome_cidade_exterior`: 1 valor(es) com espaços nas bordas
- `pais`: 96.9539% de nulos
- `cnae_fiscal_secundaria`: 52.655% de nulos
- `tipo_logradouro`: 413 valor(es) com espaços nas bordas
- `logradouro`: valor candidato a sentinela `NAO INFORMADO` em 580 linha(s) (0.0157%)
- `logradouro`: valor candidato a sentinela `0` em 28 linha(s) (0.0008%)
- `logradouro`: valor candidato a sentinela `.` em 13 linha(s) (0.0004%)
- `logradouro`: valor candidato a sentinela `00` em 9 linha(s) (0.0002%)
- `logradouro`: valor candidato a sentinela `000` em 3 linha(s) (0.0001%)
- `logradouro`: valor candidato a sentinela `-` em 3 linha(s) (0.0001%)
- `logradouro`: valor candidato a sentinela `0000` em 2 linha(s) (0.0001%)
- `logradouro`: valor candidato a sentinela `NULL` em 1 linha(s) (0.0%)
- `logradouro`: 5394 valor(es) com espaços nas bordas
- `logradouro`: 2 valor(es) com caracteres de controle
- `numero`: valor candidato a sentinela `0` em 22044 linha(s) (0.5976%)
- `numero`: valor candidato a sentinela `00` em 12499 linha(s) (0.3388%)
- `numero`: valor candidato a sentinela `000000` em 3074 linha(s) (0.0833%)
- `numero`: valor candidato a sentinela `000` em 2376 linha(s) (0.0644%)
- `numero`: valor candidato a sentinela `0000` em 842 linha(s) (0.0228%)
- `numero`: valor candidato a sentinela `00000` em 388 linha(s) (0.0105%)
- `numero`: valor candidato a sentinela `-` em 357 linha(s) (0.0097%)
- `numero`: valor candidato a sentinela `.` em 202 linha(s) (0.0055%)
- `numero`: valor candidato a sentinela `N/A` em 44 linha(s) (0.0012%)
- `numero`: valor candidato a sentinela `NULL` em 1 linha(s) (0.0%)
- `numero`: 862 valor(es) com espaços nas bordas
- `numero`: 2 valor(es) com caracteres de controle
- `complemento`: 58.9311% de nulos
- `complemento`: valor candidato a sentinela `-` em 720 linha(s) (0.0195%)
- `complemento`: valor candidato a sentinela `.` em 102 linha(s) (0.0028%)
- `complemento`: valor candidato a sentinela `0` em 100 linha(s) (0.0027%)
- `complemento`: valor candidato a sentinela `00` em 53 linha(s) (0.0014%)
- `complemento`: valor candidato a sentinela `000` em 25 linha(s) (0.0007%)
- `complemento`: valor candidato a sentinela `0000` em 15 linha(s) (0.0004%)
- `complemento`: valor candidato a sentinela `N/A` em 10 linha(s) (0.0003%)
- `complemento`: valor candidato a sentinela `00000` em 5 linha(s) (0.0001%)
- `complemento`: valor candidato a sentinela `00000000` em 2 linha(s) (0.0001%)
- `complemento`: valor candidato a sentinela `000000` em 1 linha(s) (0.0%)
- `complemento`: 85031 valor(es) com espaços nas bordas
- `complemento`: 3 valor(es) com caracteres de controle
- `bairro`: valor candidato a sentinela `-` em 156 linha(s) (0.0042%)
- `bairro`: valor candidato a sentinela `.` em 41 linha(s) (0.0011%)
- `bairro`: valor candidato a sentinela `NAO INFORMADO` em 17 linha(s) (0.0005%)
- `bairro`: valor candidato a sentinela `0` em 10 linha(s) (0.0003%)
- `bairro`: valor candidato a sentinela `N/A` em 9 linha(s) (0.0002%)
- `bairro`: valor candidato a sentinela `00` em 4 linha(s) (0.0001%)
- `bairro`: valor candidato a sentinela `NULL` em 2 linha(s) (0.0001%)
- `bairro`: valor candidato a sentinela `SEM INFORMACAO` em 1 linha(s) (0.0%)
- `bairro`: 863 valor(es) com espaços nas bordas
- `cep`: valor candidato a sentinela `00000000` em 173 linha(s) (0.0047%)
- `cep`: 169 valor(es) com espaços nas bordas
- `ddd_1`: valor candidato a sentinela `0` em 126520 linha(s) (3.4296%)
- `ddd_1`: valor candidato a sentinela `0000` em 4734 linha(s) (0.1283%)
- `ddd_1`: valor candidato a sentinela `00` em 1875 linha(s) (0.0508%)
- `ddd_1`: valor candidato a sentinela `000` em 15 linha(s) (0.0004%)
- `ddd_1`: 30 valor(es) com espaços nas bordas
- `telefone_1`: valor candidato a sentinela `0` em 133752 linha(s) (3.6257%)
- `telefone_1`: valor candidato a sentinela `00000000` em 10533 linha(s) (0.2855%)
- `telefone_1`: valor candidato a sentinela `99999999` em 10166 linha(s) (0.2756%)
- `telefone_1`: valor candidato a sentinela `00` em 22 linha(s) (0.0006%)
- `telefone_1`: valor candidato a sentinela `0000000` em 18 linha(s) (0.0005%)
- `telefone_1`: valor candidato a sentinela `0000` em 18 linha(s) (0.0005%)
- `telefone_1`: valor candidato a sentinela `00000` em 13 linha(s) (0.0004%)
- `telefone_1`: valor candidato a sentinela `000000` em 7 linha(s) (0.0002%)
- `telefone_1`: valor candidato a sentinela `000` em 4 linha(s) (0.0001%)
- `telefone_1`: 113 valor(es) com espaços nas bordas
- `ddd_2`: 91.731% de nulos
- `ddd_2`: valor candidato a sentinela `0000` em 82964 linha(s) (2.2489%)
- `ddd_2`: valor candidato a sentinela `00` em 204 linha(s) (0.0055%)
- `ddd_2`: valor candidato a sentinela `0` em 13 linha(s) (0.0004%)
- `telefone_2`: 91.7515% de nulos
- `telefone_2`: valor candidato a sentinela `00000000` em 83170 linha(s) (2.2545%)
- `telefone_2`: valor candidato a sentinela `99999999` em 449 linha(s) (0.0122%)
- `telefone_2`: valor candidato a sentinela `0` em 41 linha(s) (0.0011%)
- `telefone_2`: valor candidato a sentinela `00` em 11 linha(s) (0.0003%)
- `telefone_2`: valor candidato a sentinela `0000000` em 4 linha(s) (0.0001%)
- `telefone_2`: valor candidato a sentinela `0000` em 3 linha(s) (0.0001%)
- `telefone_2`: valor candidato a sentinela `00000` em 3 linha(s) (0.0001%)
- `telefone_2`: valor candidato a sentinela `000000` em 2 linha(s) (0.0001%)
- `ddd_fax`: 89.2751% de nulos
- `ddd_fax`: valor candidato a sentinela `0` em 100054 linha(s) (2.7122%)
- `ddd_fax`: valor candidato a sentinela `00` em 676 linha(s) (0.0183%)
- `ddd_fax`: valor candidato a sentinela `0000` em 164 linha(s) (0.0044%)
- `ddd_fax`: valor candidato a sentinela `000` em 23 linha(s) (0.0006%)
- `ddd_fax`: 5 valor(es) com espaços nas bordas
- `fax`: 89.2748% de nulos
- `fax`: valor candidato a sentinela `0` em 100116 linha(s) (2.7139%)
- `fax`: valor candidato a sentinela `00000000` em 1291 linha(s) (0.035%)
- `fax`: valor candidato a sentinela `99999999` em 371 linha(s) (0.0101%)
- `fax`: valor candidato a sentinela `0000000` em 70 linha(s) (0.0019%)
- `fax`: valor candidato a sentinela `00` em 30 linha(s) (0.0008%)
- `fax`: valor candidato a sentinela `0000` em 20 linha(s) (0.0005%)
- `fax`: valor candidato a sentinela `00000` em 17 linha(s) (0.0005%)
- `fax`: valor candidato a sentinela `000000` em 16 linha(s) (0.0004%)
- `fax`: valor candidato a sentinela `000` em 5 linha(s) (0.0001%)
- `fax`: 27 valor(es) com espaços nas bordas
- `correio_eletronico`: valor candidato a sentinela `-` em 64 linha(s) (0.0017%)
- `correio_eletronico`: valor candidato a sentinela `0` em 39 linha(s) (0.0011%)
- `correio_eletronico`: valor candidato a sentinela `.` em 33 linha(s) (0.0009%)
- `correio_eletronico`: valor candidato a sentinela `00` em 20 linha(s) (0.0005%)
- `correio_eletronico`: valor candidato a sentinela `00000` em 10 linha(s) (0.0003%)
- `correio_eletronico`: valor candidato a sentinela `000000` em 5 linha(s) (0.0001%)
- `correio_eletronico`: valor candidato a sentinela `000` em 4 linha(s) (0.0001%)
- `correio_eletronico`: valor candidato a sentinela `N/A` em 4 linha(s) (0.0001%)
- `correio_eletronico`: valor candidato a sentinela `0000` em 4 linha(s) (0.0001%)
- `correio_eletronico`: valor candidato a sentinela `0000000` em 4 linha(s) (0.0001%)
- `correio_eletronico`: valor candidato a sentinela `00000000` em 3 linha(s) (0.0001%)
- `correio_eletronico`: 30 valor(es) com espaços nas bordas
- `situacao_especial`: 99.946% de nulos
- `data_situacao_especial`: 99.946% de nulos

## Chave de negócio presumida

Colunas: `cnpj_basico, cnpj_ordem, cnpj_dv`. Chaves duplicadas: 0 (0 linhas).

## Resumo por coluna

| Coluna | Nulos (%) | Distintos (aprox.) | Tamanho mín-máx | Sentinelas | Espaços | Controle |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cnpj_basico` | 0.0 | 3551099 | 8-8 | 400 | 0 | 0 |
| `cnpj_ordem` | 0.0 | 4201 | 4-4 | 0 | 0 | 0 |
| `cnpj_dv` | 0.0 | 103 | 2-2 | 121746 | 0 | 0 |
| `identificador_matriz_filial` | 0.0 | 2 | 1-1 | 0 | 0 | 0 |
| `nome_fantasia` | 63.8559 | 1123961 | 1-55 | 175 | 1 | 2 |
| `situacao_cadastral` | 0.0 | 5 | 2-2 | 0 | 0 | 0 |
| `data_situacao_cadastral` | 0.0 | 16531 | 1-8 | 5726 | 0 | 0 |
| `motivo_situacao_cadastral` | 0.0 | 54 | 2-2 | 1418395 | 0 | 0 |
| `nome_cidade_exterior` | 99.9429 | 744 | 3-54 | 0 | 1 | 0 |
| `pais` | 96.9539 | 121 | 2-3 | 0 | 0 | 0 |
| `data_inicio_atividade` | 0.0 | 19624 | 8-8 | 0 | 0 | 0 |
| `cnae_fiscal_principal` | 0.0 | 1305 | 7-7 | 0 | 0 | 0 |
| `cnae_fiscal_secundaria` | 52.655 | 617086 | 7-791 | 0 | 0 | 0 |
| `tipo_logradouro` | 1.2506 | 358 | 3-20 | 0 | 413 | 0 |
| `logradouro` | 0.0 | 906215 | 1-60 | 639 | 5394 | 2 |
| `numero` | 0.0 | 59364 | 1-6 | 41827 | 862 | 2 |
| `complemento` | 58.9311 | 463688 | 1-156 | 1033 | 85031 | 3 |
| `bairro` | 0.0 | 171364 | 1-50 | 240 | 863 | 0 |
| `cep` | 0.2279 | 629998 | 7-8 | 173 | 169 | 0 |
| `uf` | 0.0 | 27 | 2-2 | 0 | 0 | 0 |
| `municipio` | 0.0 | 5845 | 4-4 | 0 | 0 | 0 |
| `ddd_1` | 16.5812 | 537 | 1-4 | 133144 | 30 | 0 |
| `telefone_1` | 16.5812 | 2396492 | 1-8 | 154533 | 113 | 0 |
| `ddd_2` | 91.731 | 111 | 1-4 | 83181 | 0 | 0 |
| `telefone_2` | 91.7515 | 156180 | 1-8 | 83683 | 0 | 0 |
| `ddd_fax` | 89.2751 | 455 | 1-4 | 100917 | 5 | 0 |
| `fax` | 89.2748 | 157506 | 1-8 | 101936 | 27 | 0 |
| `correio_eletronico` | 29.5225 | 2210082 | 1-82 | 190 | 30 | 0 |
| `situacao_especial` | 99.946 | 8 | 6-25 | 0 | 0 | 0 |
| `data_situacao_especial` | 99.946 | 1578 | 8-8 | 0 | 0 | 0 |

## Detalhes

### `cnpj_basico`

- Padrões de formato: `99999999` (100.0%)

### `cnpj_ordem`

- Padrões de formato: `9999` (100.0%)

### `cnpj_dv`

- Padrões de formato: `99` (100.0%)

### `identificador_matriz_filial`

- Padrões de formato: `9` (100.0%)
- Valores mais frequentes: `1` (95.5224%), `2` (4.4776%)

### `situacao_cadastral`

- Padrões de formato: `99` (100.0%)
- Valores mais frequentes: `08` (47.1924%), `02` (38.4492%), `04` (13.7674%), `03` (0.4419%), `01` (0.1492%)

### `data_situacao_cadastral`

- Padrões de formato: `99999999` (99.8448%), `9` (0.1552%)
- Datas: 3683285 válidas, 5726 zeradas, 0 inválidas; menor 1901-01-01, maior 2026-10-11

### `motivo_situacao_cadastral`

- Padrões de formato: `99` (100.0%)
- Valores mais frequentes: `00` (38.4492%), `01` (37.3244%), `63` (13.7269%), `71` (4.8006%), `67` (1.8822%), `73` (1.3227%), `54` (0.6323%), `75` (0.4382%), `02` (0.2712%), `37` (0.266%)

### `pais`

- Padrões de formato: `999` (99.968%), `99` (0.032%)

### `data_inicio_atividade`

- Padrões de formato: `99999999` (100.0%)
- Datas: 3689011 válidas, 0 zeradas, 0 inválidas; menor 1901-01-01, maior 2026-10-11

### `cnae_fiscal_principal`

- Padrões de formato: `9999999` (100.0%)

### `tipo_logradouro`

- Padrões de formato: `AAA` (67.0791%), `AAAAAAA` (23.2248%), `AAAAA` (2.3088%), `AAAAAAAA` (1.8431%), `99A AAA` (1.6122%)

### `numero`

- Padrões de formato: `999` (47.4373%), `99` (22.8894%), `9999` (14.5302%), `A/A` (4.5199%), `AA` (4.3358%)

### `cep`

- Padrões de formato: `99999999` (99.8838%), `9999999` (0.1116%), `       9` (0.0046%)

### `uf`

- Padrões de formato: `AA` (100.0%)
- Valores mais frequentes: `SP` (28.9596%), `MG` (10.8204%), `RJ` (8.4903%), `PR` (6.8289%), `RS` (6.806%), `BA` (4.9296%), `SC` (4.872%), `GO` (3.6213%), `PE` (2.9809%), `CE` (2.8237%)

### `municipio`

- Padrões de formato: `9999` (100.0%)

### `ddd_1`

- Padrões de formato: `99` (94.3024%), `9` (4.1171%), `999` (1.2927%), `9999` (0.2867%), ` 999` (0.001%)

### `telefone_1`

- Padrões de formato: `99999999` (93.8601%), `9` (4.3483%), `9999999` (1.7496%), `999999` (0.0181%), `999 9999` (0.0066%)

### `ddd_2`

- Padrões de formato: `99` (72.7949%), `9999` (27.1975%), `9` (0.0075%)

### `telefone_2`

- Padrões de formato: `99999999` (99.7453%), `9999999` (0.1321%), `999999` (0.0795%), `9` (0.0154%), `9999` (0.0145%)

### `ddd_fax`

- Padrões de formato: `99` (69.2732%), `9` (25.2923%), `999` (4.9327%), `9999` (0.5005%), ` 999` (0.0013%)

### `fax`

- Padrões de formato: `99999999` (67.5609%), `9` (25.3074%), `9999999` (6.8039%), `999999` (0.1352%), `9999 999` (0.1178%)

### `situacao_especial`

- Valores mais frequentes: `ESPOLIO EV 407` (42.1185%), `FALIDO` (30.9237%), `RECUPERACAO JUDICIAL` (18.3735%), `LIQUIDACAO EXTRA-JUDICIAL` (5.3715%), `EM LIQUIDACAO` (1.5562%), `INTERVENCAO` (1.1546%), `LIQUIDACAO JUDICIAL` (0.4016%), `EM LIQUID.EXTRA-JUDICIAL` (0.1004%)

### `data_situacao_especial`

- Padrões de formato: `99999999` (100.0%)
- Datas: 1992 válidas, 0 zeradas, 0 inválidas; menor 1977-05-23, maior 2026-10-07
