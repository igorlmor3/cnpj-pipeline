# Profiling: Empresas (2026-10)

- Arquivos Parquet: 10
- Linhas analisadas: 3.524.181
- Amostra: 5.0% das linhas (resultados aproximados)
- Dados pessoais: este relatório contém apenas agregados e padrões mascarados

## Achados automáticos

- `razao_social`: 89 valor(es) com espaços nas bordas
- `qualificacao_responsavel`: valor candidato a sentinela `00` em 124 linha(s) (0.0035%)
- `ente_federativo_responsavel`: 99.8933% de nulos

## Chave de negócio presumida

Colunas: `cnpj_basico`. Chaves duplicadas: 0 (0 linhas).

## Resumo por coluna

| Coluna | Nulos (%) | Distintos (aprox.) | Tamanho mín-máx | Sentinelas | Espaços | Controle |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cnpj_basico` | 0.0 | 3568353 | 8-8 | 0 | 0 | 0 |
| `razao_social` | 0.0 | 3474861 | 3-150 | 0 | 89 | 0 |
| `natureza_juridica` | 0.0 | 80 | 4-4 | 0 | 0 | 0 |
| `qualificacao_responsavel` | 0.0 | 31 | 2-2 | 124 | 0 | 0 |
| `capital_social` | 0.0 | 27692 | 4-15 | 0 | 0 | 0 |
| `porte_empresa` | 0.0059 | 3 | 2-2 | 0 | 0 | 0 |
| `ente_federativo_responsavel` | 99.8933 | 2441 | 4-37 | 0 | 0 | 0 |

## Detalhes

### `cnpj_basico`

- Padrões de formato: `99999999` (100.0%)

### `natureza_juridica`

- Padrões de formato: `9999` (100.0%)
- Valores mais frequentes: `2135` (64.8229%), `2062` (23.9468%), `4090` (4.2025%), `3999` (2.0403%), `2240` (1.1462%), `4120` (0.898%), `3085` (0.4787%), `4014` (0.3042%), `2305` (0.3007%), `2321` (0.2458%)

### `qualificacao_responsavel`

- Padrões de formato: `99` (100.0%)
- Valores mais frequentes: `50` (64.7971%), `49` (24.3447%), `51` (4.2025%), `16` (2.6461%), `59` (0.898%), `05` (0.8554%), `65` (0.5866%), `19` (0.4382%), `28` (0.3288%), `34` (0.3037%)

### `capital_social`

- Padrões de formato: `9999,99` (35.0652%), `9,99` (30.9263%), `99999,99` (22.899%), `999,99` (4.8567%), `999999,99` (4.8437%)
- Decimal: 3524181 no padrão `0,00`, 0 fora do padrão; maior valor 999999999999.0

### `porte_empresa`

- Padrões de formato: `99` (100.0%)
- Valores mais frequentes: `01` (75.7245%), `05` (21.2%), `03` (3.0756%)
