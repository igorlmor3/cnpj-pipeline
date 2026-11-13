"""Tabelas e consultas no Athena sobre a silver publicada no S3.

As tabelas sao externas, apontam para s3://<bucket>/silver/<tabela>/ e usam partition
projection em `reference_month`, entao nao precisam de crawler nem de MSCK REPAIR: um novo mes
publicado ja aparece nas consultas.

As colunas das tabelas saem das mesmas regras da silver (silver_rules.py), e um teste confere
que o DDL bate com o Parquet que a silver realmente grava.

Exemplos:
    python -m cnpj_pipeline.cloud.athena create-tables --print-only
    python -m cnpj_pipeline.cloud.athena create-tables
    python -m cnpj_pipeline.cloud.athena query "SELECT count(*) FROM silver_empresas"
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from typing import Any

from cnpj_pipeline.cloud import common
from cnpj_pipeline.download import normalize_tables
from cnpj_pipeline.schemas import columns_for
from cnpj_pipeline.silver_rules import RULES, output_type

log = logging.getLogger("cnpj_pipeline.cloud.athena")

ATHENA_TYPES = {
    "VARCHAR": "string",
    "DATE": "date",
    "DECIMAL(20,2)": "decimal(20,2)",
    "BOOLEAN": "boolean",
    "VARCHAR[]": "array<string>",
}
DEFAULT_START_MONTH = "2023-01"
POLL_SECONDS = 1.0
MAX_POLLS = 600


class AthenaError(RuntimeError):
    """Consulta falhou ou foi cancelada."""


def silver_columns(table: str) -> list[tuple[str, str]]:
    """Colunas (nome, tipo Athena) na mesma ordem em que a silver grava o Parquet."""
    columns = [(c, ATHENA_TYPES[output_type(RULES[table][c])]) for c in columns_for(table)]
    if table == "Estabelecimentos":
        columns.append(("cnpj", "string"))
    columns += [
        ("_source_file", "string"),
        ("_reference_month", "string"),
        ("_ingested_at", "timestamp"),
        ("_silver_processed_at", "timestamp"),
    ]
    return columns


def table_name(layer: str, table: str) -> str:
    return f"{layer}_{table.lower()}"


def create_table_ddl(
    table: str,
    *,
    bucket: str,
    database: str = common.DEFAULT_DATABASE,
    layer: str = "silver",
    start_month: str = DEFAULT_START_MONTH,
) -> str:
    cols = ",\n  ".join(f"`{name}` {kind}" for name, kind in silver_columns(table))
    location = f"s3://{bucket}/{layer}/{table.lower()}/"
    template = f"{location}reference_month=${{reference_month}}/"
    return f"""CREATE EXTERNAL TABLE IF NOT EXISTS `{database}`.`{table_name(layer, table)}` (
  {cols}
)
PARTITIONED BY (`reference_month` string)
STORED AS PARQUET
LOCATION '{location}'
TBLPROPERTIES (
  'projection.enabled' = 'true',
  'projection.reference_month.type' = 'date',
  'projection.reference_month.format' = 'yyyy-MM',
  'projection.reference_month.range' = '{start_month},NOW',
  'projection.reference_month.interval' = '1',
  'projection.reference_month.interval.unit' = 'MONTHS',
  'storage.location.template' = '{template}'
)"""


def run_query(
    client: Any,
    sql: str,
    *,
    database: str,
    workgroup: str,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Executa uma consulta e espera terminar. Retorna o id da execucao."""
    execution = client.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=workgroup,
    )["QueryExecutionId"]
    for _ in range(MAX_POLLS):
        status = client.get_query_execution(QueryExecutionId=execution)["QueryExecution"]["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            return execution
        if state in ("FAILED", "CANCELLED"):
            raise AthenaError(f"consulta {state}: {status.get('StateChangeReason', 'sem detalhe')}")
        sleep(POLL_SECONDS)
    raise AthenaError("tempo esgotado esperando a consulta terminar")


def fetch_rows(client: Any, execution: str) -> list[list[str | None]]:
    rows: list[list[str | None]] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"QueryExecutionId": execution}
        if token:
            kwargs["NextToken"] = token
        page = client.get_query_results(**kwargs)
        for row in page["ResultSet"]["Rows"]:
            rows.append([cell.get("VarCharValue") for cell in row["Data"]])
        token = page.get("NextToken")
        if not token:
            return rows


def create_tables(
    client: Any,
    *,
    bucket: str,
    database: str,
    workgroup: str,
    tables: list[str],
    start_month: str,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    for table in tables:
        ddl = create_table_ddl(table, bucket=bucket, database=database, start_month=start_month)
        run_query(client, ddl, database=database, workgroup=workgroup, sleep=sleep)
        log.info("tabela criada ou ja existente: %s.%s", database, table_name("silver", table))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.cloud.athena",
        description="Cria as tabelas da silver no Athena e executa consultas.",
    )
    common_args = argparse.ArgumentParser(add_help=False)
    common_args.add_argument(
        "--bucket", default=common.default_bucket(), help=f"Bucket (ou {common.BUCKET_ENV})"
    )
    common_args.add_argument("--database", default=common.DEFAULT_DATABASE)
    common_args.add_argument("--workgroup", default=common.DEFAULT_WORKGROUP)
    common_args.add_argument("--profile", default=common.default_profile())
    common_args.add_argument("--region", default=common.default_region())
    common_args.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-tables", parents=[common_args], help="Cria as tabelas externas")
    create.add_argument("--tables", nargs="+", metavar="TABELA", help="Subconjunto de tabelas")
    create.add_argument("--start-month", default=DEFAULT_START_MONTH, help="Primeiro mes da projecao")
    create.add_argument("--print-only", action="store_true", help="Imprime o DDL sem executar")
    query = sub.add_parser("query", parents=[common_args], help="Executa uma consulta e imprime o resultado")
    query.add_argument("sql")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, client: Any | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.command == "create-tables":
        try:
            tables = normalize_tables(args.tables)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        if not args.bucket:
            print(f"Informe --bucket ou defina {common.BUCKET_ENV}.", file=sys.stderr)
            return 2
        if args.print_only:
            for table in tables:
                print(
                    create_table_ddl(
                        table, bucket=args.bucket, database=args.database, start_month=args.start_month
                    )
                )
                print(";\n")
            return 0

    try:
        if client is None:
            client = common.make_client("athena", profile=args.profile, region=args.region)
        if args.command == "create-tables":
            create_tables(
                client,
                bucket=args.bucket,
                database=args.database,
                workgroup=args.workgroup,
                tables=tables,
                start_month=args.start_month,
            )
        else:
            execution = run_query(client, args.sql, database=args.database, workgroup=args.workgroup)
            for row in fetch_rows(client, execution):
                print("\t".join("" if v is None else v for v in row))
    except (common.CloudConfigError, AthenaError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
