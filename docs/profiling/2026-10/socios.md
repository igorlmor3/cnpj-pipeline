# Profiling: Socios (2026-10)

- Arquivos Parquet: 10
- Linhas analisadas: 1.425.050
- Amostra: 5.0% das linhas (resultados aproximados)
- Dados pessoais: este relatório contém apenas agregados e padrões mascarados

## Achados automáticos

- `cnpj_basico`: valor candidato a sentinela `00000000` em 1 linha(s) (0.0001%)
- `qualificacao_socio`: valor candidato a sentinela `00` em 1 linha(s) (0.0001%)
- `pais`: 99.6691% de nulos
- `nome_representante`: 97.3504% de nulos
- `qualificacao_representante_legal`: valor candidato a sentinela `00` em 1387262 linha(s) (97.3483%)
- `faixa_etaria`: valor candidato a sentinela `0` em 37660 linha(s) (2.6427%)

## Resumo por coluna

| Coluna | Nulos (%) | Distintos (aprox.) | Tamanho mín-máx | Sentinelas | Espaços | Controle |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cnpj_basico` | 0.0 | 1363403 | 8-8 | 1 | 0 | 0 |
| `identificador_socio` | 0.0 | 3 | 1-1 | 0 | 0 | 0 |
| `nome_socio` | 0.0062 | 1266124 | 3-142 | 0 | 0 | 0 |
| `cnpj_cpf_socio` | 0.0458 | 649534 | 8-11 | 0 | 0 | 0 |
| `qualificacao_socio` | 0.0 | 39 | 2-2 | 1 | 0 | 0 |
| `data_entrada_sociedade` | 0.0 | 16702 | 8-8 | 0 | 0 | 0 |
| `pais` | 99.6691 | 112 | 3-3 | 0 | 0 | 0 |
| `representante_legal` | 0.0 | 31379 | 11-11 | 0 | 0 | 0 |
| `nome_representante` | 97.3504 | 32009 | 5-59 | 0 | 0 | 0 |
| `qualificacao_representante_legal` | 0.0 | 7 | 2-2 | 1387262 | 0 | 0 |
| `faixa_etaria` | 0.0 | 10 | 1-1 | 37660 | 0 | 0 |

## Detalhes

### `cnpj_basico`

- Padrões de formato: `99999999` (100.0%)

### `identificador_socio`

- Padrões de formato: `9` (100.0%)
- Valores mais frequentes: `2` (97.3637%), `1` (2.5905%), `3` (0.0458%)

### `cnpj_cpf_socio`

- Padrões de formato: `***999999**` (97.4083%), `99999999` (2.5917%)

### `qualificacao_socio`

- Padrões de formato: `99` (100.0%)
- Valores mais frequentes: `49` (63.7735%), `22` (21.5888%), `16` (5.3129%), `05` (2.8939%), `59` (1.5273%), `65` (1.4503%), `10` (1.386%), `30` (0.5245%), `52` (0.3024%), `31` (0.256%)

### `data_entrada_sociedade`

- Padrões de formato: `99999999` (100.0%)
- Datas: 1425050 válidas, 0 zeradas, 0 inválidas; menor 1900-01-01, maior 2026-10-10

### `pais`

- Padrões de formato: `999` (100.0%)

### `representante_legal`

- Padrões de formato: `***999999**` (100.0%)

### `qualificacao_representante_legal`

- Padrões de formato: `99` (100.0%)
- Valores mais frequentes: `00` (97.3483%), `05` (1.7764%), `17` (0.3399%), `15` (0.2737%), `14` (0.2507%), `09` (0.0084%), `35` (0.0027%)

### `faixa_etaria`

- Padrões de formato: `9` (100.0%)
- Valores mais frequentes: `5` (24.3988%), `6` (19.8011%), `4` (17.8203%), `7` (15.3229%), `8` (8.1262%), `3` (7.0193%), `9` (4.238%), `0` (2.6427%), `2` (0.5319%), `1` (0.0987%)
