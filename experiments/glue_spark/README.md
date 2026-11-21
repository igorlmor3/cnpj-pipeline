# Experimento: o fato e os marts da gold com Spark na AWS Glue

Esta pasta compara o DuckDB, que o projeto usa, com o Spark em um caso concreto: construir a camada gold de um mês a partir da silver. A pergunta é quanto tempo e quanto dinheiro cada um gasta no mesmo trabalho, com o resultado conferido linha a linha.

O DuckDB já foi medido no mês 2026-10 (na tarefa do ECS Fargate de 4 vCPUs, a gold inteira levou 5 min 53 s; veja [infra/README.md](../../infra/README.md#execução-medida-2026-10)). Aqui o mesmo trabalho roda em um job da AWS Glue, com Spark 3.5.

## O que o job constrói

O fato `fato_estabelecimento` (junção de 73,8 milhões de estabelecimentos com Empresas e Simples) e os quatro marts que dependem dele (`mart_aberturas_mensal`, `mart_estabelecimentos_ativos`, `mart_perfil_porte_natureza` e `mart_simples_mei_uf`), mais as quatro dimensões que esses marts usam. O mart de sócios fica de fora, porque exigiria os nomes dos sócios na nuvem. O fato responde por mais de 95% do tempo da gold no DuckDB, então a comparação não perde nada de relevante com essa omissão.

## Como o experimento evita comparar regras diferentes

- **O SQL é o mesmo.** O job importa o `gold_models.py` do projeto e executa o mesmo SQL. A única tradução está em `to_spark` (`strftime` vira `date_format` e `VARCHAR` vira `STRING`). Qualquer outra sintaxe que o Spark não aceite faz o job falhar, e não produzir um resultado diferente em silêncio.
- **O resultado é conferido contra o do DuckDB.** Com `--verify`, o job lê a gold que o DuckDB publicou no S3 e compara cada modelo: tabelas de até 2 milhões de linhas por `exceptAll` nos dois sentidos, e o fato por contagem e soma de `xxhash64` das linhas. O job termina com erro se algo diferir.
- **Uma diferença conhecida.** A mediana do capital social em grupos de tamanho par é a média dos dois valores do meio. O DuckDB trunca o resultado no centavo, e o Spark o arredonda. A conferência aceita 1 centavo de diferença nessa coluna (`capital_social_mediano`) e linha a linha no resto da tabela. Um teste confirma que uma diferença maior que 1 centavo é detectada.
- **A conferência foi validada antes de ir para a nuvem.** O teste `tests/test_glue_spark.py` roda o job em Spark local sobre os dados sintéticos do projeto e confere as 9 tabelas contra a gold do DuckDB; outro teste altera um valor da gold de referência e exige que a conferência o encontre. Esses testes precisam de `pyspark` e Java e são pulados onde não existem.

## A entrada

A silver completa tem nomes de sócios, endereços e contatos, e o projeto a mantém fora da nuvem. O fato e os marts usam 11 colunas de Estabelecimentos, 4 de Empresas, 3 de Simples e as tabelas pequenas de Naturezas e Municípios. `make_input.py` grava só essas colunas, no layout da silver, e é só isso que sobe para o S3. As colunas estão em `INPUT_COLUMNS`, em `spark_gold.py`, e um teste garante que nenhuma é nome, endereço ou contato.

## Como rodar

1. Crie o job (em `infra/terraform.tfvars`, acrescente `glue_enabled = true`):

   ```powershell
   cd infra
   terraform plan
   terraform apply
   cd ..
   ```

   O `plan` mostra a criação do papel IAM, da política, de dois objetos no S3 (o script e o `gold_models.py`) e do job, e a alteração da regra de ciclo de vida do bucket, que passa a expirar `experiments/` depois de 30 dias.

2. Monte a entrada a partir da silver local e envie para o S3:

   ```powershell
   python experiments/glue_spark/make_input.py --month 2026-10
   $bucket = terraform -chdir=infra output -raw bucket
   aws s3 cp data/experiments/spark_input "s3://$bucket/experiments/spark/input" --recursive --profile cnpj-publisher
   ```

3. Rode o job com a conferência (esta execução prova que o resultado é igual):

   ```powershell
   $env:CNPJ_S3_BUCKET = $bucket
   python experiments/glue_spark/run_job.py --profile cnpj-admin --month 2026-10 --workers 5 --verify
   ```

   O comando espera o fim e mostra o estado, o tempo de execução, os DPU-segundos e o custo a preço de lista. O job grava em `experiments/spark/output/` os modelos e um `_resumo_<data>.json` com o tempo de cada modelo, a versão do Spark e, com `--verify`, o resultado da conferência.

4. Rode sem a conferência, com outros números de workers, para medir tempo e custo de cada configuração (a conferência lê os dados de novo e entra na cobrança):

   ```powershell
   python experiments/glue_spark/run_job.py --profile cnpj-admin --month 2026-10 --workers 2
   python experiments/glue_spark/run_job.py --profile cnpj-admin --month 2026-10 --workers 10
   ```

5. Leia os resumos gravados:

   ```powershell
   aws s3 ls "s3://$bucket/experiments/spark/output/" --profile cnpj-admin
   aws s3 cp "s3://$bucket/experiments/spark/output/_resumo_<data>.json" - --profile cnpj-admin
   ```

## O que cada número significa

- **Tempo de construção** (`construcao_segundos`, no resumo) é o tempo dos modelos dentro do job, sem a partida do Spark e sem a conferência. É o número comparável ao da gold no DuckDB.
- **Tempo de execução** (`Tempo de execucao`, no disparador) é o que a Glue informa e inclui a partida do job.
- **Custo** é calculado pelos DPU-segundos que a Glue informa, a US$ 0,44 por DPU-hora (preço de lista, cobrado por segundo, com mínimo de 1 minuto por execução). Um worker G.1X é 1 DPU, com 4 vCPUs e 16 GB, e um dos workers é o driver.

## Limites da comparação

- O DuckDB leu o Parquet do disco local da tarefa, e o Spark lê do S3 pela rede. Parte da diferença de tempo vem daí.
- O DuckDB rodou em um nó de 4 vCPUs. No Spark, o total de vCPUs depende do número de workers, e a comparação mais direta é a de custo por execução, não a de CPUs.
- Cada configuração tem uma ou poucas execuções. A variação entre execuções não foi medida em mais repetições, então diferenças pequenas não são conclusivas.
- O DuckDB construiu os 13 modelos da gold. O job Spark constrói 9. Os 4 que ficam de fora (as dimensões restantes e o mart de sócios) levam segundos no DuckDB.
