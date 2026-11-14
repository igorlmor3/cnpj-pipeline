# Profiling: Simples (2026-10)

- Arquivos Parquet: 1
- Linhas analisadas: 2.540.202
- Amostra: 5.0% das linhas (resultados aproximados)
- Dados pessoais: este relatório contém apenas agregados e padrões mascarados

## Achados automáticos

- `data_exclusao_simples`: valor candidato a sentinela `00000000` em 1260627 linha(s) (49.627%)
- `data_opcao_mei`: valor candidato a sentinela `00000000` em 670067 linha(s) (26.3785%)
- `data_exclusao_mei`: valor candidato a sentinela `00000000` em 1535192 linha(s) (60.4358%)

## Chave de negócio presumida

Colunas: `cnpj_basico`. Chaves duplicadas: 0 (0 linhas).

## Resumo por coluna

| Coluna | Nulos (%) | Distintos (aprox.) | Tamanho mín-máx | Sentinelas | Espaços | Controle |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cnpj_basico` | 0.0 | 2603794 | 8-8 | 0 | 0 | 0 |
| `opcao_pelo_simples` | 0.0 | 2 | 1-1 | 0 | 0 | 0 |
| `data_opcao_simples` | 0.0 | 6786 | 8-8 | 0 | 0 | 0 |
| `data_exclusao_simples` | 0.0 | 6607 | 8-8 | 1260627 | 0 | 0 |
| `opcao_pelo_mei` | 0.0 | 2 | 1-1 | 0 | 0 | 0 |
| `data_opcao_mei` | 0.0 | 6212 | 8-8 | 670067 | 0 | 0 |
| `data_exclusao_mei` | 0.0 | 5833 | 8-8 | 1535192 | 0 | 0 |

## Detalhes

### `cnpj_basico`

- Padrões de formato: `99999999` (100.0%)

### `opcao_pelo_simples`

- Padrões de formato: `A` (100.0%)
- Valores mais frequentes: `S` (51.2721%), `N` (48.7279%)

### `data_opcao_simples`

- Padrões de formato: `99999999` (100.0%)
- Datas: 2540202 válidas, 0 zeradas, 0 inválidas; menor 2007-07-01, maior 2026-10-11

### `data_exclusao_simples`

- Padrões de formato: `99999999` (100.0%)
- Datas: 1279575 válidas, 1260627 zeradas, 0 inválidas; menor 2007-07-01, maior 2026-12-31

### `opcao_pelo_mei`

- Padrões de formato: `A` (100.0%)
- Valores mais frequentes: `N` (65.255%), `S` (34.745%)

### `data_opcao_mei`

- Padrões de formato: `99999999` (100.0%)
- Datas: 1870135 válidas, 670067 zeradas, 0 inválidas; menor 2009-07-01, maior 2026-10-11

### `data_exclusao_mei`

- Padrões de formato: `99999999` (100.0%)
- Datas: 1005010 válidas, 1535192 zeradas, 0 inválidas; menor 2009-07-01, maior 2026-12-31
