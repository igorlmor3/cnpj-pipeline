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

Decisões em aberto, registradas em [docs/architecture.md](docs/architecture.md): motor de transformação (DuckDB, Polars ou PySpark), orquestração (Airflow ou Prefect) e uso de dbt na camada gold.

## Estrutura do repositório

```
.
├── data/                  dados locais (não versionados)
│   ├── raw/
│   ├── bronze/
│   ├── silver/
│   └── gold/
├── docs/
│   ├── architecture.md    decisões de arquitetura
│   └── data_quality_rules.md   catálogo de problemas de qualidade e decisões
├── src/cnpj_pipeline/
│   ├── config.py          caminhos e configuração
│   └── download.py        download parametrizado com retomada e manifesto
├── tests/
├── .github/workflows/ci.yml
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

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
- [ ] Profiling das tabelas e preenchimento do catálogo de qualidade de dados
- [ ] Esquemas das tabelas a partir do dicionário de layout do mês processado
- [ ] Camada bronze (CSV para Parquet, leitura em streaming)
- [ ] Camada silver (tipagem, tratamento de sentinelas, quarentena)
- [ ] Camada gold e análises
- [ ] Testes de qualidade de dados automatizados
- [ ] Orquestração
- [ ] Benchmarks documentados

## Dados e licença

Os dados são públicos e pertencem à Receita Federal do Brasil. Este repositório não contém dados, apenas código. A licença do código ainda será definida.
