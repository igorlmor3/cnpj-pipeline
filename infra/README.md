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

## Custos e cuidados

- Armazenamento e consultas custam centavos neste volume: nas medições do mês real, uma consulta sobre o fato inteiro (73,81 milhões de linhas) leu 39,52 MB, e uma sobre um mart leu menos de 1 KB. O Athena cobra por dado lido; o limite por consulta do workgroup e o filtro por `reference_month` evitam surpresas.
- Não crie NAT Gateway nem outros recursos cobrados por hora sem necessidade.
- Confira o gasto em Billing and Cost Management de vez em quando, mesmo com o alerta ativo.
- Para desmontar tudo: `terraform destroy` dentro de `infra/`. O bucket é esvaziado junto, porque os dados podem ser regenerados pelo pipeline.

## O que nunca vai para o Git

`terraform.tfvars`, `*.tfstate`, a pasta `.terraform/`, qualquer chave de acesso ou arquivo `.env`. O `.gitignore` já cobre isso. O arquivo `.terraform.lock.hcl`, gerado pelo `terraform init`, deve ser versionado.

## Próximo nível

O nível 2 (pipeline rodando em container no ECS Fargate, com acesso por papel IAM e sem chaves) acrescenta recursos a este mesmo código. Está descrito em [../docs/architecture.md](../docs/architecture.md) e ainda não foi iniciado.
