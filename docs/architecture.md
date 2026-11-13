# Arquitetura e decisões

## Camadas

| Camada | Conteúdo | Regras |
| --- | --- | --- |
| raw | Arquivos originais da Receita (zip), com manifesto | Imutável. Nunca editar. |
| bronze | Parquet com todas as colunas como texto | Sem regras de negócio. Apenas muda o formato e adiciona metadados de ingestão (arquivo de origem, mês de referência). Linhas malformadas vão para `_rejects/`. |
| silver | Tipos corretos, datas e decimais tratados, sentinelas anuladas | Linhas inválidas vão para quarentena com o motivo da rejeição. |
| gold | Modelos analíticos | Construídos a partir da silver. |

## Princípios

- Idempotência: cada arquivo de saída é gravado em um arquivo temporário e renomeado ao final, então uma falha nunca deixa um Parquet pela metade. Reexecutar pula o que já existe, e `--force` refaz.
- Reprodutibilidade: o manifesto do raw identifica exatamente quais arquivos foram usados.
- Memória limitada: nenhuma etapa carrega uma tabela inteira em memória. Leitura em streaming ou por partes.
- Desenvolvimento em amostra: toda etapa aceita um recorte (uma tabela, uma parte, um mês) para iterar rápido.

## Decisões em aberto

Cada decisão é registrada aqui com as alternativas consideradas e o motivo.

| Tema | Alternativas | Status |
| --- | --- | --- |
| Motor de transformação | DuckDB, Polars, PySpark | Decidido: DuckDB. Lê CSV maior que a memória, grava Parquet direto e roda local, sem servidor. |
| Orquestração | Airflow, Prefect, cron + scripts | Em aberto |
| Modelagem gold | dbt (com DuckDB), SQL puro | Em aberto |
| Particionamento do Parquet | por UF, por mês de referência, sem partição | Em aberto |
| Testes de qualidade | dbt tests, Great Expectations, pytest | Em aberto |
