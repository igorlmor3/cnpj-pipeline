"""Tabelas e consultas no Athena sobre a silver e a gold publicadas no S3.

As tabelas sao externas, apontam para s3://<bucket>/<camada>/<tabela>/ e usam partition
projection em `reference_month`, entao nao precisam de crawler nem de MSCK REPAIR: um novo mes
publicado ja aparece nas consultas. As tabelas se chamam silver_<tabela> e gold_<modelo>.

As colunas da silver saem das mesmas regras da silver (silver_rules.py). As da gold saem do
manifesto local da gold (data/gold/_manifest_<mes>.json), que registra o tipo de cada coluna do
Parquet gravado. Testes conferem que o DDL bate com o Parquet que cada camada realmente grava.

Exemplos:
    python -m cnpj_pipeline.cloud.athena create-tables --print-only
    python -m cnpj_pipeline.cloud.athena create-tables
    python -m cnpj_pipeline.cloud.athena create-tables --layer gold --month 2026-10
    python -m cnpj_pipeline.cloud.athena query "SELECT count(*) FROM silver_empresas"
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cnpj_pipeline import config
from cnpj_pipeline.cloud import common
from cnpj_pipeline.download import MONTH_RE, normalize_tables
from cnpj_pipeline.gold_models import MODEL_NAMES
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
SIMPLE_ATHENA_TYPES = {
    "VARCHAR": "string",
    "DATE": "date",
    "BOOLEAN": "boolean",
    "INTEGER": "int",
    "BIGINT": "bigint",
    "DOUBLE": "double",
    "TIMESTAMP": "timestamp",
    "VARCHAR[]": "array<string>",
}
DECIMAL_RE = re.compile(r"^DECIMAL\((\d+),\s*(\d+)\)$")
DEFAULT_START_MONTH = "2023-01"
POLL_SECONDS = 1.0
MAX_POLLS = 600


class AthenaError(RuntimeError):
    """Consulta falhou ou foi cancelada."""


def athena_type(duckdb_type: str) -> str:
    """Tipo do Athena para um tipo do DuckDB, como o DESCRIBE o informa."""
    if duckdb_type in SIMPLE_ATHENA_TYPES:
        return SIMPLE_ATHENA_TYPES[duckdb_type]
    match = DECIMAL_RE.match(duckdb_type)
    if match:
        return f"decimal({match.group(1)},{match.group(2)})"
    raise AthenaError(f"tipo sem equivalente no Athena: {duckdb_type}")


def gold_columns(gold_dir: Path, month: str, models: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Colunas (nome, tipo Athena) de cada modelo da gold, lidas do manifesto do mes."""
    path = gold_dir / f"_manifest_{month}.json"
    if not path.exists():
        raise AthenaError(
            f"manifesto da gold nao encontrado: {path}. Rode: python -m cnpj_pipeline.gold --month {month}"
        )
    built = json.loads(path.read_text(encoding="utf-8")).get("models", {})
    missing = [m for m in models if m not in built]
    if missing:
        raise AthenaError(f"modelos ausentes no manifesto da gold de {month}: {', '.join(missing)}")
    return {m: [(c, athena_type(t)) for c, t in built[m]["columns"].items()] for m in models}


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


def drop_table_ddl(table: str, *, database: str = common.DEFAULT_DATABASE, layer: str = "silver") -> str:
    """Remove so a definicao da tabela no catalogo; os arquivos no S3 nao sao tocados."""
    return f"DROP TABLE IF EXISTS `{database}`.`{table_name(layer, table)}`"


def create_table_ddl(
    table: str,
    *,
    bucket: str,
    database: str = common.DEFAULT_DATABASE,
    layer: str = "silver",
    start_month: str = DEFAULT_START_MONTH,
    columns: list[tuple[str, str]] | None = None,
) -> str:
    """DDL da tabela externa. Sem `columns`, usa as colunas da silver."""
    cols = ",\n  ".join(f"`{name}` {kind}" for name, kind in (columns or silver_columns(table)))
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
    layer: str = "silver",
    columns: dict[str, list[tuple[str, str]]] | None = None,
    replace: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Cria as tabelas externas. Com `replace`, apaga a definicao antes (use quando as colunas mudarem)."""
    for table in tables:
        if replace:
            run_query(
                client,
                drop_table_ddl(table, database=database, layer=layer),
                database=database,
                workgroup=workgroup,
                sleep=sleep,
            )
        ddl = create_table_ddl(
            table,
            bucket=bucket,
            database=database,
            layer=layer,
            start_month=start_month,
            columns=(columns or {}).get(table),
        )
        run_query(client, ddl, database=database, workgroup=workgroup, sleep=sleep)
        log.info(
            "tabela %s: %s.%s",
            "recriada" if replace else "criada ou ja existente",
            database,
            table_name(layer, table),
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.cloud.athena",
        description="Cria as tabelas da silver e da gold no Athena e executa consultas.",
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
    create.add_argument(
        "--layer", choices=("silver", "gold"), default="silver", help="Camada (padrao: %(default)s)"
    )
    create.add_argument("--month", help="Mes AAAA-MM do manifesto da gold (obrigatorio com --layer gold)")
    create.add_argument("--gold-dir", type=Path, default=config.GOLD_DIR)
    create.add_argument(
        "--tables",
        nargs="+",
        metavar="TABELA",
        help="Subconjunto de tabelas da silver ou de modelos da gold",
    )
    create.add_argument("--start-month", default=DEFAULT_START_MONTH, help="Primeiro mes da projecao")
    create.add_argument("--print-only", action="store_true", help="Imprime o DDL sem executar")
    create.add_argument(
        "--replace",
        action="store_true",
        help="Apaga a definicao de cada tabela antes de criar (necessario quando as colunas mudam; "
        "os arquivos no S3 nao sao tocados)",
    )
    query = sub.add_parser("query", parents=[common_args], help="Executa uma consulta e imprime o resultado")
    query.add_argument("sql")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, client: Any | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    columns: dict[str, list[tuple[str, str]]] | None = None
    if args.command == "create-tables":
        try:
            if args.layer == "gold":
                tables = args.tables or list(MODEL_NAMES)
                unknown = [t for t in tables if t not in MODEL_NAMES]
                if unknown:
                    raise ValueError(
                        f"Modelo(s) desconhecido(s): {', '.join(unknown)}. Validos: {', '.join(MODEL_NAMES)}"
                    )
                if not args.month or not MONTH_RE.match(args.month):
                    raise ValueError("--layer gold exige --month AAAA-MM (mes do manifesto da gold)")
            else:
                tables = normalize_tables(args.tables)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        if not args.bucket:
            print(f"Informe --bucket ou defina {common.BUCKET_ENV}.", file=sys.stderr)
            return 2
        if args.layer == "gold":
            try:
                columns = gold_columns(args.gold_dir, args.month, tables)
            except AthenaError as exc:
                print(exc, file=sys.stderr)
                return 2
        if args.print_only:
            for table in tables:
                if args.replace:
                    print(drop_table_ddl(table, database=args.database, layer=args.layer))
                    print(";\n")
                print(
                    create_table_ddl(
                        table,
                        bucket=args.bucket,
                        database=args.database,
                        layer=args.layer,
                        start_month=args.start_month,
                        columns=(columns or {}).get(table),
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
                layer=args.layer,
                columns=columns,
                replace=args.replace,
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
