# Arquitetura e decisões

## Camadas

| Camada | Conteúdo | Regras |
| --- | --- | --- |
| raw | Arquivos originais da Receita (zip), com manifesto | Imutável. Nunca editar. |
| bronze | Parquet com todas as colunas como texto | Sem regras de negócio. Apenas muda o formato e adiciona metadados de ingestão (arquivo de origem, mês de referência). Linhas malformadas vão para `_rejects/`. |
| silver | Tipos corretos, datas e decimais tratados, sentinelas anuladas | Regras por coluna em `silver_rules.py`. Linhas com chave inválida ou duplicada vão para `data/silver/_quarantine/` com o motivo (`_reject_reason`). |
| gold | Dimensões, fato por estabelecimento e marts agregados | Construída a partir da silver por consultas SQL em `gold_models.py`. Sem dados de contato, endereço nem nomes. Verificações de qualidade bloqueantes ao final (chave única, contagens contra a silver, soma dos marts). |

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
| Deduplicação na silver | Manter a primeira linha, manter a mais recente, rejeitar todas as repetidas | Manter uma linha, a mais preenchida (mais colunas não nulas depois da limpeza), com desempate pelo hash do conteúdo (determinístico), e quarentenar as demais. Só o hash mantinha, em Empresas, um registro vazio e descartava o completo; a regra de preenchimento corrige isso. |
| Orquestração | Airflow, Prefect, cron + scripts | Em aberto |
| Modelagem gold | dbt (com DuckDB), SQL puro | Decidido: SQL puro do DuckDB, em um módulo Python, com o mesmo padrão de manifesto e execução da silver. Menos peças para um projeto de um mês de dados e um autor; dbt pode entrar se a quantidade de modelos crescer. |
| Particionamento do Parquet | por UF, por mês de referência, sem partição | Em aberto |
| Testes de qualidade | dbt tests, Great Expectations, pytest | Parcial: a gold roda verificações próprias a cada execução e os testes unitários usam pytest. Cobertura dos dados da silver e do mês real em aberto. |

## Decisões de plataforma e de produto

Estas decisões foram tomadas no planejamento. O nível 1 da AWS (Terraform, publicação no S3 e tabelas no Athena) tem código com testes de clientes simulados e já foi aplicado em uma conta real, com a gold do mês 2026-10 publicada e consultada no Athena; a primeira análise está feita, e o nível 2, o agente e o site ainda não foram iniciados. Cada decisão registra o motivo, para ser revisada quando houver dado medido.

| Tema | Alternativas | Decisão e motivo |
| --- | --- | --- |
| Nuvem | AWS, Azure, GCP | AWS. É a mais pedida em vagas de engenharia de dados no Brasil e amplia o portfólio com um serviço de armazenamento, catálogo e consulta serverless. |
| Escopo na nuvem | Só publicar os dados (nível 1), rodar o pipeline na nuvem (nível 2) | Nível 1 primeiro, nível 2 depois. O nível 1 entrega valor com pouco retrabalho, e quase tudo dele (bucket, catálogo, IAM) é reaproveitado no nível 2. |
| Infraestrutura | Console manual, Terraform, CloudFormation | Terraform desde o nível 1, para que o nível 2 apenas acrescente recursos ao mesmo código. |
| Camada de armazenamento no código | `Path` local espalhado, módulo de armazenamento | Módulo único de armazenamento (local e `s3://`), para o nível 2 não exigir mudanças na lógica das camadas. No nível 1 a publicação ficou isolada em `cloud/publish.py`, sem alterar as camadas; o módulo de armazenamento fica para o nível 2. |
| Credenciais | Chaves no código ou no `.env`, perfil local, papel IAM | Perfil local no nível 1 e papel IAM da tarefa no nível 2. Nenhuma credencial no repositório. |
| Consulta na nuvem | Athena, Redshift, DuckDB sobre S3 | Athena com catálogo Glue: serverless, sem custo fixo, cobrança por dado lido. Parquet particionado reduz o que é lido. |
| Descoberta de partições | Crawler do Glue, MSCK REPAIR, partition projection | Partition projection em `reference_month`: sem crawler (que cobra por tempo) e sem passo manual a cada mês novo. |
| Chaves do usuário do pipeline | Criadas pelo Terraform, criadas no console | Criadas no console, para que nunca fiquem no arquivo de estado do Terraform. |
| Controle de custo | Sem controle, alertas, limites | Alerta de orçamento no primeiro dia, região de menor custo, sem NAT Gateway, limite de dados lidos por consulta e destruição do ambiente quando não estiver em uso. |
| Agente de IA | RAG clássico com embeddings, texto para SQL | Texto para SQL. O dado é tabular e as perguntas pedem contagens exatas, que embeddings não fornecem. Embeddings só para partes pequenas, como descrições de CNAE e natureza jurídica. |
| Segurança do agente | Executar qualquer SQL, validar antes de executar | Somente leitura, apenas tabelas permitidas da gold, com tempo e número de linhas máximos e validação da consulta antes de executar. |
| Abuso e custo do agente público | Aberto, com limites | Limite de perguntas por IP, teto mensal de gasto no provedor do modelo e perguntas de exemplo que funcionam sem chamar o modelo. |
| Privacidade | Expor tudo, expor agregados | Apenas agregados nas saídas públicas. Sem consulta por pessoa física e sem nomes de sócios. |
| Site | React com Tailwind, outras opções | React com Tailwind, com gráficos a partir de agregados pré-calculados (JSON) gerados pelo pipeline, o que dispensa servidor por visita. |
| Hospedagem do site | S3 e CloudFront, GitHub Pages, Vercel, Cloudflare Pages | Hospedagem estática com plano gratuito fora da AWS, porque o plano gratuito da AWS encerra a conta em 6 meses e o site precisa continuar no ar. |
