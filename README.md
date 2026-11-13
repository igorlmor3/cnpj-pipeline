# cnpj-pipeline

Pipeline ETL em lote sobre os dados abertos do CNPJ da Receita Federal do Brasil. O objetivo do projeto é tratar um dataset público grande (alvo de 5 a 10 GB de dado bruto) com problemas reais de qualidade: arquivos sem cabeçalho, separador `;`, encoding `ISO-8859-1`, vírgula decimal, datas em texto e valores sentinela.

Status: em desenvolvimento. A seção [Roadmap](#roadmap) mostra o que já existe e o que falta.

## Fonte dos dados

- Catálogo: https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj
- Repositório de arquivos da Receita Federal: https://arquivos.receitafederal.gov.br/ (Dados > Cadastros > CNPJ)

Os arquivos são publicados mensalmente como `.zip` com CSVs. As tabelas são Empresas, Estabelecimentos e Sócios (divididas em várias partes) e as tabelas de domínio Cnaes, Motivos, Municípios, Naturezas, Países, Qualificações e Simples.

A Receita alterou o layout da página e o caminho dos arquivos no início de 2026. O template de URL usado pelo script é configurável (`CNPJ_URL_TEMPLATE` ou `--url-template`) e deve ser confirmado no site oficial antes do primeiro download. O dicionário de layout publicado junto com os arquivos de cada mês é a referência para os esquemas das tabelas.

## Arquitetura planejada

```
Receita Federal (zip/CSV)
        |
        v
data/raw      arquivos originais, imutáveis, com manifesto (tamanho e SHA-256)
        |
        v
data/bronze   CSV convertido para Parquet, sem regras de negócio, tudo como texto
        |
        v
data/silver   tipos corrigidos, datas e decimais tratados, sentinelas viram nulo,
              registros rejeitados vão para uma tabela de quarentena
        |
        v
data/gold     modelos analíticos (por exemplo, empresas ativas por município e CNAE)
```

O motor de transformação é o DuckDB. Decisões ainda em aberto, registradas em [docs/architecture.md](docs/architecture.md): orquestração (Airflow ou Prefect) e uso de dbt na camada gold.

## Estrutura do repositório

```
.
├── data/                  dados locais (não versionados)
│   ├── raw/
│   ├── bronze/
│   ├── silver/            Parquet tipado, mais _quarantine/ com as linhas rejeitadas
│   └── gold/
├── docs/
│   ├── architecture.md    decisões de arquitetura
│   ├── data_quality_rules.md   catálogo de problemas de qualidade e decisões
│   ├── profiling/         relatórios gerados pelo profiling (apenas agregados)
│   └── silver/            relatório das regras aplicadas na silver (apenas contagens)
├── src/cnpj_pipeline/
│   ├── config.py          caminhos e configuração
│   ├── download.py        download parametrizado com retomada e manifesto
│   ├── schemas.py         colunas de cada tabela (layout a validar contra o dicionário do mês)
│   ├── bronze.py          zip/CSV para Parquet com DuckDB
│   ├── profiling.py       relatórios de qualidade de dados sobre a bronze
│   ├── silver_rules.py    regras de limpeza e tipagem por coluna (provisórias)
│   ├── silver.py          bronze para silver: tipos, limpeza, quarentena e deduplicação
│   ├── sample.py          gerador de zips sintéticos com defeitos plantados
│   └── demo.py            amostra, bronze, profiling e silver de ponta a ponta
├── tests/
├── .github/workflows/ci.yml
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## Demonstração rápida (sem baixar nada)

Para ver o pipeline inteiro funcionando em poucos segundos, sem os 8 GB reais:

```bash
python -m cnpj_pipeline.demo
```

O comando gera zips sintéticos no formato dos arquivos da Receita, com defeitos plantados de propósito (espaços nas bordas, `;` e aspas dentro de textos, decimais com ponto, datas zeradas e inválidas, chaves duplicadas, órfãs e inválidas, linhas com número de colunas errado), converte tudo para Parquet na bronze, roda o profiling e constrói a camada silver. A saída fica em `data/sample/`, separada dos dados reais. Os relatórios em `data/sample/profiling/2000-01/` mostram os defeitos sendo encontrados, e `data/sample/silver_report/` mostra o que a silver corrigiu. Todos os dados são inventados e não correspondem a pessoas ou empresas reais.

O mesmo fluxo roda como teste automatizado no CI a cada push.

## Como executar

### Com Docker

```bash
cp .env.example .env
docker compose build
docker compose run --rm pipeline python -m cnpj_pipeline.download --month 2026-11 --dry-run
docker compose run --rm pipeline python -m cnpj_pipeline.download --month 2026-11
docker compose run --rm pipeline pytest
```

### Sem Docker

Requer Python 3.11 ou superior.

```bash
python -m venv .venv
source .venv/bin/activate        # no Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m cnpj_pipeline.download --month 2026-11 --dry-run
```

## Download

`python -m cnpj_pipeline.download` baixa os arquivos de um mês de referência para `data/raw/<AAAA-MM>/`.

| Opção | Descrição |
| --- | --- |
| `--month AAAA-MM` | Mês de referência (obrigatório) |
| `--tables T [T ...]` | Subconjunto de tabelas, sem diferenciar maiúsculas (padrão: todas) |
| `--parts N` | Número de partes das tabelas divididas (padrão: 10) |
| `--out-dir PASTA` | Pasta de destino (padrão: `data/raw`) |
| `--url-template URL` | Template com `{month}` e `{filename}` |
| `--workers N` | Downloads em paralelo (padrão: 2) |
| `--retries N` | Tentativas por arquivo (padrão: 5) |
| `--no-checksum` | Não calcula o SHA-256 |
| `--dry-run` | Lista as URLs sem baixar |
| `--manifest-only` | Não acessa a rede: gera o manifesto a partir dos arquivos já presentes na pasta do mês |

O script retoma downloads interrompidos (arquivo `.part` e cabeçalho HTTP `Range`), repete tentativas com backoff exponencial, confere o tamanho final contra o `Content-Length` e grava `manifest.json` com tamanho e SHA-256 de cada arquivo. Partes que retornam 404 em tabelas divididas são ignoradas com aviso. Se nenhuma parte de uma tabela for obtida, o script termina com código de saída diferente de zero.

Se os arquivos já foram baixados manualmente, coloque-os em `data/raw/<AAAA-MM>/` e rode `python -m cnpj_pipeline.download --month AAAA-MM --manifest-only` para gerar o manifesto sem acessar a rede. Rodar o download normal com o mesmo mês também funciona: arquivos completos são reconhecidos, pulados e incluídos no manifesto.

## Camada bronze

`python -m cnpj_pipeline.bronze --month AAAA-MM` lê os zips de `data/raw/<AAAA-MM>/` e grava Parquet em `data/bronze/<tabela>/reference_month=<AAAA-MM>/`.

| Opção | Descrição |
| --- | --- |
| `--month AAAA-MM` | Mês de referência (obrigatório) |
| `--tables T [T ...]` | Subconjunto de tabelas (padrão: todas as que tiverem zip na pasta) |
| `--force` | Refaz arquivos que já existem na bronze (por padrão são pulados) |
| `--memory-limit` | Limite de memória do DuckDB; o excedente vai para disco (padrão: 4GB) |
| `--threads N` | Threads do DuckDB (padrão: automático) |
| `--max-reject-ratio` | Fração máxima de linhas rejeitadas por arquivo antes de falhar (padrão: 0,001) |

Comportamento:

- Todas as colunas são gravadas como texto. Tipagem e regras de negócio ficam para a camada silver.
- O encoding latin-1 é convertido para UTF-8, e campos vazios viram NULL.
- Cada CSV é extraído do zip em streaming para uma pasta temporária (`data/tmp`, apagada ao final), porque o DuckDB não lê CSV de dentro de zip.
- Linhas com número de colunas errado ou aspas inválidas não são descartadas em silêncio: vão para `data/bronze/_rejects/` com o número da linha, o texto original e o motivo. Se a fração de rejeitadas passar do limite, o arquivo falha e o Parquet é removido.
- Antes de converter, a quantidade de colunas mais frequente entre os primeiros 1000 registros é comparada com `schemas.py`. Algumas linhas quebradas no início não reprovam o arquivo, mas uma mudança real de layout para o processamento com uma mensagem clara.
- O dialeto do CSV (separador, aspas, colunas, encoding) é informado explicitamente ao DuckDB, sem detecção automática. Isso é mais rápido, determinístico e aceita arquivos com fim de linha LF ou CRLF.
- Cada Parquet inclui `_source_file`, `_reference_month` e `_ingested_at`. O diretório `reference_month=...` segue a convenção Hive, então o DuckDB também expõe `reference_month` ao ler a pasta inteira.
- Um manifesto `data/bronze/_manifest_<mes>.json` registra linhas, rejeitadas, tamanhos e tempo de cada arquivo. Esses números alimentam a tabela de resultados abaixo.

Os nomes das colunas em `schemas.py` seguem o layout histórico e precisam ser conferidos contra o dicionário de layout do mês processado.

## Profiling

`python -m cnpj_pipeline.profiling --month AAAA-MM` lê a camada bronze e gera, para cada tabela, `docs/profiling/<mês>/<tabela>.md` e `.json`, mais `referential.md` e `.json`.

Por coluna, o relatório mede nulos, valores distintos (aproximado), tamanho do texto, espaços nas bordas, caracteres de controle e de substituição (problema de encoding), padrões de formato (dígitos viram `9`, letras viram `A`), valores candidatos a sentinela, validade de datas `AAAAMMDD` e formato de decimais com vírgula. Por tabela, mede duplicidade da chave de negócio presumida e, entre tabelas, chaves órfãs nas relações presumidas (por exemplo, estabelecimentos sem empresa correspondente).

| Opção | Descrição |
| --- | --- |
| `--tables T [T ...]` | Subconjunto de tabelas (padrão: todas com Parquet na bronze) |
| `--sample-percent N` | Perfila só N% das linhas, para tabelas grandes (resultados aproximados) |
| `--skip-referential` | Pula a checagem de integridade referencial, que é a etapa mais pesada |
| `--memory-limit` | Limite de memória do DuckDB (padrão: 4GB) |
| `--out-dir` | Pasta dos relatórios (padrão: `docs/profiling`) |

Os relatórios contêm apenas agregados. Valores literais aparecem somente para colunas categóricas de baixa sensibilidade (códigos e indicadores). Nomes, documentos, endereços e contatos aparecem só como padrões mascarados e contagens, então os relatórios podem ser versionados.

Os achados são candidatos, não veredictos. Um valor como `00000000` em `cnpj_basico`, por exemplo, pode ser legítimo, e cada caso precisa de decisão humana antes de virar regra em `docs/data_quality_rules.md`.

Se uma tabela foi processada só em parte (nem todas as partes), as chaves órfãs refletem a ausência de arquivos e não um problema nos dados.

## Camada silver

`python -m cnpj_pipeline.silver --month AAAA-MM` lê a bronze (tudo texto) e grava `data/silver/<tabela>/reference_month=<mês>/<tabela>.parquet` com tipos corretos.

O que a silver faz, tabela por tabela:

- Datas `AAAAMMDD` viram `DATE`; zeros e datas inexistentes (como 31 de fevereiro) viram NULL.
- Decimais com vírgula viram `DECIMAL(20,2)`; valores inválidos viram NULL.
- Indicadores `S`/`N` viram `BOOLEAN`; a lista de CNAEs secundários vira `VARCHAR[]`.
- Textos são limpos (caracteres de controle, espaços repetidos e nas bordas); vazio vira NULL. Códigos mantêm os zeros à esquerda.
- UF fora das 27 siglas (mais `EX`) vira NULL; e-mail vai para minúsculas.
- Chaves obrigatórias inválidas (por exemplo `cnpj_basico` fora de 8 posições alfanuméricas) enviam a linha para `data/silver/_quarantine/` com o motivo em `_reject_reason`.
- Chaves de negócio duplicadas mantêm uma linha, escolhida de forma determinística (hash do conteúdo, então a ordem de entrada não importa), e as demais vão para a quarentena como `duplicate_key`.
- Estabelecimentos ganha a coluna derivada `cnpj` (14 posições).

Se a fração de linhas em quarentena de uma tabela passar de `--max-quarantine-ratio` (padrão 1%), a tabela falha e a saída silver é removida; a quarentena fica para inspeção. O manifesto `data/silver/_manifest_<mês>.json` e o relatório `docs/silver/<mês>.md` registram, por coluna, quantos valores foram anulados ou alterados (apenas contagens, sem valores).

| Opção | Descrição |
| --- | --- |
| `--tables T [T ...]` | Subconjunto de tabelas (padrão: todas com Parquet na bronze) |
| `--force` | Refaz tabelas que já existem na silver |
| `--max-quarantine-ratio` | Fração máxima em quarentena por tabela (padrão: 0.01) |
| `--memory-limit` | Limite de memória do DuckDB (padrão: 4GB) |
| `--report-dir` | Pasta do relatório (padrão: `docs/silver`) |

As regras estão em `silver_rules.py` e são provisórias: partem do layout histórico e de hipóteses, e só viram regras definitivas depois de confirmadas pelo profiling do mês real.

## Qualidade de dados

Cada problema encontrado nos dados é registrado em [docs/data_quality_rules.md](docs/data_quality_rules.md) com a decisão tomada (corrigir, anular ou rejeitar), a camada em que a regra é aplicada e o teste que a cobre. O catálogo é preenchido durante o profiling e não parte de suposições.

## Resultados medidos

A ser preenchido após a primeira execução completa, com números reais.

| Métrica | Valor |
| --- | --- |
| Mês de referência processado | |
| Tamanho dos zips (raw) | |
| Tamanho descompactado | |
| Linhas por tabela | |
| Tempo por etapa | |
| Pico de memória | |
| Máquina usada | |

## Roadmap

- [x] Estrutura do repositório, CI, Docker
- [x] Download parametrizado com retomada e manifesto
- [x] Módulo de profiling (testado com dados sintéticos)
- [ ] Rodar o profiling no mês real e preencher o catálogo de qualidade de dados
- [ ] Esquemas das tabelas a partir do dicionário de layout do mês processado
- [x] Camada bronze (CSV para Parquet em streaming, testada com dados sintéticos)
- [ ] Validar a bronze com o mês real e registrar as métricas
- [x] Camada silver (tipagem, limpeza, quarentena e deduplicação, testada com dados sintéticos)
- [ ] Confirmar as regras da silver contra o profiling do mês real
- [x] Gerador de amostra sintética e demo ponta a ponta, testados no CI
- [ ] Camada gold e análises
- [ ] Testes de qualidade de dados automatizados
- [ ] Orquestração
- [ ] Benchmarks documentados

## Dados e licença

Os dados são públicos e pertencem à Receita Federal do Brasil. Este repositório não contém dados, apenas código. A licença do código ainda será definida.
