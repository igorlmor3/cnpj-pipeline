# Infraestrutura na AWS (nível 1)

Este diretório descreve, em Terraform, o ambiente de nuvem do projeto. No nível 1 o pipeline continua rodando na máquina local e publica o Parquet no S3; o Athena consulta esses dados na nuvem.

Estado: o código foi escrito e a parte em Python tem testes automatizados com clientes simulados. O Terraform já foi aplicado em uma conta real (9 recursos) e a gold do mês 2026-10 foi publicada e consultada no Athena. Para o usuário que aplica o Terraform, anexe uma política com permissão sobre S3, Glue, Athena, IAM e Budgets; sem ela, o `apply` falha com `AccessDenied`, e uma permissão recém-anexada pode levar alguns instantes para valer em todos os serviços. Leia sempre o plano antes de aplicar.

## O que é criado

| Recurso | Para quê |
| --- | --- |
| Bucket S3 | Data lake privado, com acesso público bloqueado e criptografia. Apaga resultados do Athena após 7 dias e uploads incompletos após 7 dias. |
| Banco `cnpj` no Glue | Catálogo das tabelas. As tabelas em si são criadas por `python -m cnpj_pipeline.cloud.athena create-tables`. |
| Workgroup `cnpj-pipeline` no Athena | Grava resultados no bucket e cancela consultas que leiam mais que o limite (padrão 5 GB). |
| Usuário IAM `cnpj-pipeline-publisher` | Menor privilégio: só este bucket, este workgroup e este banco do Glue. |
| Orçamento mensal | Alerta por e-mail em 80% do gasto real e em 100% do previsto (padrão US$ 5). |

As chaves de acesso do usuário `cnpj-pipeline-publisher` não são criadas pelo Terraform, de propósito: assim elas nunca ficam gravadas no arquivo de estado.

## Pré-requisitos

- Conta AWS com MFA no usuário raiz e um usuário administrador (`cnpj-admin`) configurado no AWS CLI com `aws configure --profile cnpj-admin`.
- AWS CLI e Terraform instalados.
- `aws sts get-caller-identity --profile cnpj-admin` funcionando.

## Passo a passo

1. Crie o arquivo de variáveis (ele não é versionado):

   ```powershell
   cd infra
   Copy-Item terraform.tfvars.example terraform.tfvars
   ```

   Edite `terraform.tfvars`: o nome do bucket precisa ser único em toda a AWS, e `alert_email` recebe os alertas.

2. Inicialize e confira o plano:

   ```powershell
   terraform init
   terraform plan
   ```

3. Aplique:

   ```powershell
   terraform apply
   ```

   Se o nome do bucket já existir, escolha outro. Se o orçamento ou outro recurso for recusado pelo plano gratuito, anote a mensagem de erro: ela diz qual serviço está bloqueado.

4. Crie a chave de acesso do usuário do pipeline no console: IAM > Users > `cnpj-pipeline-publisher` > Security credentials > Create access key (tipo CLI). Depois configure um perfil separado:

   ```powershell
   aws configure --profile cnpj-publisher
   ```

   Use a região `us-east-1`. Nunca coloque essas chaves em arquivos do repositório.

5. Instale o suporte à AWS no projeto e informe o bucket:

   ```powershell
   cd ..
   python -m pip install -e ".[aws]"
   $env:CNPJ_S3_BUCKET = "o-nome-do-seu-bucket"
   ```

## Publicar e consultar

```powershell
# Veja o que seria enviado, sem enviar nada (a gold tem cerca de 1,5 GiB e nenhum nome de pessoa)
python -m cnpj_pipeline.cloud.publish --month 2026-10 --layers gold --dry-run

# Envie a gold. A silver (padrão do comando) tem nomes de sócios; envie-a só se precisar dela na nuvem
python -m cnpj_pipeline.cloud.publish --month 2026-10 --layers gold

# Crie as tabelas da gold no Athena (uma vez; as colunas vêm do manifesto local da gold) e consulte
python -m cnpj_pipeline.cloud.athena create-tables --layer gold --month 2026-10
python -m cnpj_pipeline.cloud.athena query "SELECT uf, empresas_ativas, pct_simples, pct_mei FROM gold_mart_simples_mei_uf WHERE reference_month = '2026-10' AND NOT is_organizacao_politica ORDER BY empresas_ativas DESC LIMIT 5"
```

Para a silver, use `--layers silver` (ou omita `--layers`) e `create-tables` sem `--layer`; as tabelas se chamam `silver_<tabela>`. As da gold se chamam `gold_<modelo>`.

A publicação é idempotente: um arquivo já enviado, com o mesmo tamanho e o mesmo SHA-256, é pulado, e cada envio é conferido por tamanho depois de concluído. Quarentena e rejeitados contêm linhas originais, possivelmente com dados pessoais, e só sobem com `--include-quarantine`.

As tabelas usam partition projection em `reference_month`, então um novo mês publicado aparece nas consultas sem crawler e sem `MSCK REPAIR`. Sempre filtre por `reference_month` nas consultas para ler menos dados e gastar menos.

## API pública do agente

A API deixa o site fazer perguntas livres ao agente de texto para SQL sem expor a chave do provedor. Ela é opcional (`api_enabled`, desligado por padrão) e fica em `modules/api/`. O desenho do agente e das camadas de defesa está em [../docs/agent_design.md](../docs/agent_design.md).

| Recurso | Para quê |
| --- | --- |
| Função Lambda `cnpj-pipeline-api` (Python 3.12) com endereço público (Function URL) | Recebe `POST /ask` com `{"question": "..."}` e responde com a resposta em texto e o resultado da última consulta. `GET /health` mostra se o interruptor está ligado. |
| Tabela DynamoDB `cnpj-pipeline-api-state` | Contadores de limite por visitante (por minuto e por dia), contador global do dia e gasto do mês. Os contadores expiram sozinhos (TTL). |
| Parâmetro `/cnpj-pipeline/api/enabled` | Interruptor. Nasce desligado, e só o valor `true` liga. Se o parâmetro não puder ser lido, a API fica desligada. |
| Parâmetro `/cnpj-pipeline/api/salt` (SecureString) | Sal do identificador anônimo do visitante: o IP nunca é gravado, só um HMAC dele. |
| Parâmetro `/cnpj-pipeline/api/anthropic-api-key` (SecureString) | Chave do provedor. É gravada pela CLI, fora do Terraform, para nunca entrar no arquivo de estado. |
| Papel IAM da função | Menor privilégio: lê só a gold (a silver, que tem nomes de sócios, fica fora), grava só os resultados do Athena, usa só esta tabela e só estes parâmetros. |
| Grupo de logs (14 dias) | Uma linha de JSON por pergunta, sem pergunta, SQL, chave nem IP (ver [Observabilidade](#observabilidade-da-api)). |
| Consultas salvas, métricas, alarmes e painel do CloudWatch | Mostram uso, latência, gasto e fidelidade dos números a partir do registro, só com dados agregados. |

Limites padrão: 3 perguntas por minuto e 15 por dia por visitante, 150 por dia no total e US$ 4 por mês de gasto com o modelo (o limite do workspace no provedor, US$ 5, é a barreira final). O prazo de uma pergunta é de 40 segundos, e a função tem 90 segundos de tempo máximo.

### Publicar

1. Gere o pacote (o código, as dependências para Linux e o arquivo de colunas da gold do mês; o arquivo só tem nomes e tipos de coluna, nenhum dado):

   ```powershell
   python -m cnpj_pipeline.api.package --month 2026-10
   ```

2. Em `infra/terraform.tfvars`, ligue a API e informe o endereço do site (origem `https`, sem barra no final):

   ```
   api_enabled         = true
   api_month           = "2026-10"
   api_months          = ["2026-07", "2026-08", "2026-09", "2026-10"]
   api_allowed_origins = ["https://seu-usuario.github.io"]
   ```

3. Aplique (o `init -upgrade` atualiza o provedor da AWS para a série 6, necessária para liberar a chamada pública da função, e baixa o provedor `random`, usado para gerar o sal):

   ```powershell
   cd infra
   terraform init -upgrade
   terraform plan
   terraform apply
   ```

4. Grave a chave do provedor no Parameter Store, sem ela aparecer na tela:

   ```powershell
   $k = Read-Host "Cole a chave aqui"
   aws ssm put-parameter --profile cnpj-admin --name "/cnpj-pipeline/api/anthropic-api-key" --type SecureString --value $k
   Remove-Variable k
   ```

5. Ligue o interruptor e teste:

   ```powershell
   aws ssm put-parameter --profile cnpj-admin --name "/cnpj-pipeline/api/enabled" --type String --value true --overwrite
   $url = terraform output -raw api_url
   Invoke-RestMethod "${url}health"
   $r = Invoke-RestMethod -Method Post -Uri "${url}ask" -ContentType "application/json" -Body '{"question": "Quais são as 5 UFs com mais empresas ativas?"}'
   $r.status
   $r.message
   $r.rows | ConvertTo-Json -Compress
   ```

Para desligar a API na hora, mude o mesmo parâmetro para `false` (o valor é relido em até 30 segundos). O Terraform não volta o valor do interruptor. Depois de atualizar o código, gere o pacote de novo e rode `terraform apply`: a função só é atualizada se o conteúdo mudou, porque o zip é determinístico.

### Observabilidade da API

A função escreve uma linha de JSON por pergunta na saída padrão (e não pelo `logging`, porque o formato de texto da Lambda poria um prefixo antes da linha, e só JSON puro é descoberto pelo Logs Insights e aceito pelos filtros de métrica). A linha tem o status, o código HTTP, o identificador anônimo do visitante (8 caracteres do HMAC), o tempo em milissegundos, as chamadas ao modelo, o número de consultas SQL e de buscas na documentação, os tokens, o custo em dólares, os bytes lidos no Athena e, nas respostas, quantos números do texto foram conferidos e quantos não têm apoio nos resultados. Pergunta, SQL, resposta, chave e IP nunca entram nela.

O arquivo `modules/api/observability.tf` cria, a partir dessa linha:

| Recurso | O que mostra |
| --- | --- |
| 7 consultas salvas (CloudWatch, Logs Insights, Consultas salvas, prefixo `cnpj-pipeline/api/`) | Perguntas por dia e status; latência (p50, p95 e p99); custo e tokens por dia; fidelidade dos números; uso das ferramentas (SQL, busca na documentação, chamadas ao modelo, bytes lidos); recusas por status; limites por motivo. |
| 5 métricas (`CnpjPipeline/Api`) | `Asks`, `Errors`, `BudgetHits`, `CostUsd` e `AnswersWithUnsupportedNumbers`. |
| 3 alarmes | Gasto do dia acima de `api_daily_cost_alarm_usd` (padrão US$ 1); três ou mais erros do modelo ou internos em uma hora; teto de gasto do mês atingido. |
| Painel `cnpj-pipeline-api` | Perguntas, erros e teto atingido por dia, gasto por dia, respostas com número sem apoio, latência e perguntas por status. O nome sai de `terraform output api_dashboard`. |

Com `api_alarm_email` preenchido em `terraform.tfvars`, um tópico SNS envia os alarmes por e-mail (a AWS manda um pedido de confirmação que precisa ser aceito uma vez). Sem ele, os alarmes existem e aparecem só no console. As 5 métricas, os 3 alarmes e o painel cabem no que o nível gratuito do CloudWatch cobre (10 métricas personalizadas, 10 alarmes e 3 painéis); as consultas do Logs Insights são cobradas por dado lido, e o grupo de logs tem poucos megabytes. Um teste confere que cada campo usado nas consultas e nos filtros existe na linha de registro e que nenhum campo sensível é usado.

### Limites conhecidos

- O endereço da função é público, sem WAF. Pedidos feitos fora de um navegador não carregam o cabeçalho de origem, então o que os limita são os contadores. Cada pedido barrado custa uma escrita no DynamoDB, e o gasto com o modelo só acontece depois de passar por todos os limites.
- `api_reserved_concurrency` limita as execuções simultâneas, mas é opcional: contas novas têm um limite total baixo de execuções, e a AWS exige que 10 fiquem sem reserva.
- A API responde só a partir de agregados (a gold). Sem pergunta de mês, lê `api_month` (o padrão, o mais recente); com `api_months`, o agente também compara os meses da lista. Para um mês novo, acrescente-o à lista e rode `terraform apply`: a tabela do Athena já enxerga o mês novo (partition projection), e a função relê a lista na próxima execução a frio.

## Custos e cuidados

- Armazenamento e consultas custam centavos neste volume: nas medições do mês real, uma consulta sobre o fato inteiro (73,81 milhões de linhas) leu 39,52 MB, e uma sobre um mart leu menos de 1 KB. O Athena cobra por dado lido; o limite por consulta do workgroup e o filtro por `reference_month` evitam surpresas.
- Não crie NAT Gateway nem outros recursos cobrados por hora sem necessidade.
- Confira o gasto em Billing and Cost Management de vez em quando, mesmo com o alerta ativo.
- Para desmontar tudo: `terraform destroy` dentro de `infra/`. O bucket é esvaziado junto, porque os dados podem ser regenerados pelo pipeline.

## O que nunca vai para o Git

`terraform.tfvars`, `*.tfstate`, a pasta `.terraform/`, qualquer chave de acesso ou arquivo `.env`. O `.gitignore` já cobre isso. O arquivo `.terraform.lock.hcl`, gerado pelo `terraform init`, deve ser versionado.

## Nível 2: o pipeline no ECS Fargate

Estado: o código da tarefa e do disparador tem testes com clientes simulados, e a imagem e o módulo Terraform (`modules/ecs/`) passaram na checagem de formatação e de sintaxe. O módulo ainda não foi aplicado em uma conta, e a tarefa ainda não rodou na AWS.

O nível 2 roda o mesmo pipeline (download, bronze, silver, gold, qualidade, publicação e tabelas no Athena) em uma tarefa do ECS Fargate. No lugar de chaves de acesso, a tarefa usa as credenciais temporárias do papel IAM dela. As camadas ficam no disco de trabalho da tarefa e só a gold é publicada no S3, como no nível 1. A exportação para o site é pulada (`--skip-site`), porque a pasta do site some com o container.

| Recurso | Para que serve |
| --- | --- |
| Repositório no ECR `cnpj-pipeline` | Imagens do pipeline. Só as 3 mais recentes ficam guardadas, e cada envio passa por varredura de vulnerabilidades. |
| Cluster `cnpj-pipeline` | Agrupa a tarefa. Não tem custo enquanto nada roda. |
| Definição da tarefa `cnpj-pipeline` | 4 vCPUs, 16 GiB de memória e 120 GiB de disco de trabalho (`ecs_vcpu`, `ecs_memory_mib` e `ecs_ephemeral_storage_gib`), com limite de 300 minutos (`ecs_task_timeout_minutes`). |
| Papel de execução | Só puxa a imagem e escreve os logs. |
| Papel da tarefa | A mesma política do usuário publicador: este bucket, este workgroup do Athena e este banco do Glue, sem acesso a mais nada. |
| Grupo de logs `/ecs/cnpj-pipeline` | Logs da tarefa, com 14 dias de retenção. |
| Rede | VPC padrão, sem NAT Gateway. A tarefa sai para a internet por um IP público, e o grupo de segurança não aceita nenhuma entrada. |

O dimensionamento vem do que foi medido no mês real: o DuckDB trabalhou dentro do limite de 4 GB de memória (pico de 4.050 MiB), e um mês ocupa cerca de 47 GiB de disco (7,3 GiB de zips, 26,9 GiB de CSV, 5,2 GiB de bronze, 5,7 GiB de silver e 1,5 GiB de gold). Por isso o disco é de 120 GiB e cada tarefa processa um mês.

### Como usar

1. Em `terraform.tfvars`, acrescente `ecs_enabled = true` e aplique. O `plan` deve mostrar só criações:

   ```powershell
   cd infra
   terraform plan
   terraform apply
   ```

2. Construa a imagem e envie para o ECR (o `docker login` recebe a senha pela entrada padrão, sem mostrá-la):

   ```powershell
   $repo = terraform output -raw ecs_repository_url
   cd ..
   aws ecr get-login-password --profile cnpj-admin --region us-east-1 | docker login --username AWS --password-stdin $repo.Split("/")[0]
   docker build -f Dockerfile.cloud -t "${repo}:latest" .
   docker push "${repo}:latest"
   ```

3. Informe ao disparador onde está a tarefa (os valores saem do `terraform output`):

   ```powershell
   $env:CNPJ_ECS_CLUSTER = terraform -chdir=infra output -raw ecs_cluster
   $env:CNPJ_ECS_TASK_DEFINITION = terraform -chdir=infra output -raw ecs_task_definition
   $env:CNPJ_ECS_SUBNETS = (terraform -chdir=infra output -json ecs_subnets | ConvertFrom-Json) -join ","
   $env:CNPJ_ECS_SECURITY_GROUP = terraform -chdir=infra output -raw ecs_security_group
   ```

4. Antes de gastar com um mês inteiro, teste se o servidor da Receita responde a partir da AWS. O teste baixa só os 4 primeiros bytes de um arquivo pequeno e confere se são de um zip, porque o firewall da Receita pode responder 200 com uma página HTML de recusa:

   ```powershell
   python -m cnpj_pipeline.cloud.run_task --profile cnpj-admin --probe --month 2026-10 --wait
   ```

5. Rode um mês e acompanhe os logs. O disparador usa o perfil `cnpj-admin`, porque o usuário publicador não tem permissão de ECS:

   ```powershell
   python -m cnpj_pipeline.cloud.run_task --profile cnpj-admin --month 2026-10 --wait
   aws logs tail /ecs/cnpj-pipeline --follow --profile cnpj-admin
   ```

   Com `--wait`, o comando devolve o código de saída da tarefa (0 se tudo passou). `--force` refaz as camadas que já existem, e `--timeout-minutes` muda o limite desta execução.

### Custo e limites

- Preço de lista no us-east-1 (Linux x86, [página de preços do Fargate](https://aws.amazon.com/fargate/pricing/)): US$ 0,04048 por vCPU-hora, US$ 0,004445 por GB-hora e US$ 0,000111 por GB-hora de disco acima de 20 GiB, mais o IPv4 público (US$ 0,005 por hora). A tarefa de 4 vCPUs, 16 GiB e 120 GiB custa cerca de US$ 0,25 por hora, cobrados por segundo, e o limite de 300 minutos põe um teto de cerca de US$ 1,25 por execução.
- O ECS não limita a duração de uma tarefa. O limite de tempo é aplicado pela própria tarefa (`CNPJ_TASK_TIMEOUT_MINUTES`): passado o prazo, ela encerra a execução com o código 124 e o Fargate para de cobrar.
- Antes de começar, a tarefa confere o espaço livre no disco (`CNPJ_MIN_FREE_GIB`, padrão 60 GiB) e recusa a execução se não houver.
- Um mês por tarefa: as camadas de um mês somam cerca de 47 GiB, e a tarefa não apaga um mês para processar o seguinte.
- Para desmontar o nível 2, volte `ecs_enabled` para `false` e aplique. O repositório de imagens é esvaziado junto.
