"""Executores de consulta: DuckDB local (gold em Parquet) e Athena, com a mesma interface.

Todo SQL passa pela guarda antes de chegar a um executor. Cada executor aplica um tempo maximo
(cancelando a consulta) e devolve valores simples (texto, numero, booleano ou nulo), prontos para
serem mostrados ao modelo. Mensagens de erro do motor sao limpas de caminhos e ARNs antes de sair.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from cnpj_pipeline.agent import sql_guard
from cnpj_pipeline.agent.sql_guard import GuardResult, SqlRejected

DEFAULT_TIMEOUT_S = 30.0
POLL_SECONDS = 0.5
MAX_ERROR_CHARS = 400
MAX_CELL_CHARS = 200

_SENSITIVE = re.compile(
    r"(s3://\S+|arn:aws[\w:\-/.*]*|\b\d{12}\b|[A-Za-z]:\\\S+|/(?:home|Users|mnt|tmp)/\S+)"
)


class QueryFailed(RuntimeError):
    """A consulta passou pela guarda, mas o motor falhou ou estourou o tempo. A mensagem e segura."""


def sanitize_error(message: str) -> str:
    """Tira caminhos, URIs do S3, ARNs e numeros de conta, e limita o tamanho."""
    cleaned = _SENSITIVE.sub("<omitido>", " ".join(str(message).split()))
    return cleaned[:MAX_ERROR_CHARS]


def simple_value(value: Any) -> Any:
    """Converte para um valor simples. Decimal vira texto, para nao perder precisao."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)[:MAX_CELL_CHARS]


@dataclass(frozen=True)
class RawResult:
    columns: list[str]
    rows: list[list[Any]]
    bytes_scanned: int | None = None


@dataclass(frozen=True)
class QueryResult:
    sql: str  # o SQL que foi de fato executado (depois da guarda)
    columns: list[str]
    rows: list[list[Any]]
    may_be_truncated: bool  # o limite de linhas imposto pelo programa foi atingido (pode haver mais linhas)
    bytes_scanned: int | None
    elapsed_s: float
    tables: tuple[str, ...] = field(default_factory=tuple)


class Executor(Protocol):
    dialect: str

    def run(self, sql: str, *, max_rows: int) -> RawResult: ...


def run_query(
    sql: str,
    *,
    executor: Executor,
    month: str,
    months: tuple[str, ...] | None = None,
    max_rows: int = sql_guard.MAX_ROWS,
    clock: Callable[[], float] = time.monotonic,
) -> QueryResult:
    """Guarda, executa e devolve o resultado. Levanta SqlRejected ou QueryFailed (mensagens seguras)."""
    guarded: GuardResult = sql_guard.guard(
        sql, month=month, months=months, max_rows=max_rows, write=executor.dialect
    )
    started = clock()
    raw = executor.run(guarded.sql, max_rows=guarded.limit)
    rows = [[simple_value(c) for c in row] for row in raw.rows[: guarded.limit]]
    return QueryResult(
        sql=guarded.sql,
        columns=list(raw.columns),
        rows=rows,
        # Um LIMIT menor escolhido pela propria consulta (por exemplo, as 5 maiores) nao e truncamento.
        may_be_truncated=guarded.capped and len(rows) >= guarded.limit,
        bytes_scanned=raw.bytes_scanned,
        elapsed_s=clock() - started,
        tables=guarded.tables,
    )


__all__ = [
    "AthenaExecutor",
    "DuckDBExecutor",
    "Executor",
    "QueryFailed",
    "QueryResult",
    "RawResult",
    "SqlRejected",
    "run_query",
    "sanitize_error",
    "simple_value",
]


# --------------------------------------------------------------------------- #
# DuckDB local
# --------------------------------------------------------------------------- #


class DuckDBExecutor:
    """Roda sobre a gold local em Parquet. A conexao so enxerga a pasta da gold e fica travada."""

    dialect = "duckdb"

    def __init__(self, gold_dir: Path, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        import duckdb  # so aqui: o pacote do agente publicado (Lambda) nao precisa do DuckDB

        self._duckdb = duckdb
        self.timeout_s = timeout_s
        self.con = duckdb.connect()
        gold_dir = gold_dir.resolve()
        for name in sorted(sql_guard.ALLOWED_TABLES):
            model = name.removeprefix("gold_")
            pattern = (gold_dir / model / "*" / f"{model}.parquet").as_posix()
            self.con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{pattern}', hive_partitioning = true)"
            )
        self.con.execute(f"SET allowed_directories = ['{gold_dir.as_posix()}']")
        self.con.execute("SET enable_external_access = false")
        self.con.execute("SET lock_configuration = true")

    def run(self, sql: str, *, max_rows: int) -> RawResult:
        timer = threading.Timer(self.timeout_s, self.con.interrupt)
        timer.start()
        try:
            cursor = self.con.execute(sql)
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchmany(max_rows)
        except self._duckdb.InterruptException as exc:
            raise QueryFailed(
                f"A consulta passou do tempo máximo de {self.timeout_s:g} s e foi cancelada."
            ) from exc
        except self._duckdb.Error as exc:
            raise QueryFailed(sanitize_error(str(exc))) from exc
        finally:
            timer.cancel()
        return RawResult(columns=columns, rows=[list(r) for r in rows])

    def close(self) -> None:
        self.con.close()


# --------------------------------------------------------------------------- #
# Athena
# --------------------------------------------------------------------------- #

_INT_TYPES = {"tinyint", "smallint", "integer", "int", "bigint"}
_FLOAT_TYPES = {"float", "double", "real"}


def _cast(value: str | None, athena_type: str) -> Any:
    if value is None:
        return None
    kind = athena_type.lower().split("(")[0]
    try:
        if kind in _INT_TYPES:
            return int(value)
        if kind in _FLOAT_TYPES:
            return float(value)
        if kind == "boolean":
            return value.lower() == "true"
    except ValueError:
        return value
    return value  # decimal, texto e datas ficam como texto, sem perder precisao


class AthenaExecutor:
    """Roda no Athena, no workgroup do projeto, com tempo maximo e cancelamento."""

    dialect = "trino"

    def __init__(
        self,
        client: Any,
        *,
        database: str,
        workgroup: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        poll_s: float = POLL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.database = database
        self.workgroup = workgroup
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self.sleep = sleep
        self.clock = clock

    def _wait(self, execution: str) -> dict[str, Any]:
        deadline = self.clock() + self.timeout_s
        while True:
            info = self.client.get_query_execution(QueryExecutionId=execution)["QueryExecution"]
            state = info["Status"]["State"]
            if state == "SUCCEEDED":
                return info
            if state in ("FAILED", "CANCELLED"):
                reason = info["Status"].get("StateChangeReason", "sem detalhe")
                raise QueryFailed(sanitize_error(f"O Athena recusou a consulta ({state}): {reason}"))
            if self.clock() >= deadline:
                self.client.stop_query_execution(QueryExecutionId=execution)
                raise QueryFailed(
                    f"A consulta passou do tempo máximo de {self.timeout_s:g} s e foi cancelada."
                )
            self.sleep(self.poll_s)

    def run(self, sql: str, *, max_rows: int) -> RawResult:
        try:
            execution = self.client.start_query_execution(
                QueryString=sql,
                QueryExecutionContext={"Database": self.database},
                WorkGroup=self.workgroup,
            )["QueryExecutionId"]
        except Exception as exc:  # erros do cliente (credencial, rede, workgroup) viram mensagem segura
            raise QueryFailed(sanitize_error(f"Não foi possível iniciar a consulta: {exc}")) from exc
        info = self._wait(execution)
        scanned = info.get("Statistics", {}).get("DataScannedInBytes")

        columns: list[str] = []
        types: list[str] = []
        rows: list[list[Any]] = []
        token: str | None = None
        first_page = True
        while len(rows) < max_rows:
            kwargs: dict[str, Any] = {"QueryExecutionId": execution, "MaxResults": min(max_rows + 1, 1000)}
            if token:
                kwargs["NextToken"] = token
            page = self.client.get_query_results(**kwargs)
            if first_page:
                meta = page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
                columns = [c["Name"] for c in meta]
                types = [c["Type"] for c in meta]
            page_rows = page["ResultSet"]["Rows"]
            if first_page:
                page_rows = page_rows[1:]  # a primeira linha da primeira pagina repete os nomes das colunas
                first_page = False
            for row in page_rows:
                cells = [c.get("VarCharValue") for c in row["Data"]]
                rows.append([_cast(v, t) for v, t in zip(cells, types, strict=False)])
            token = page.get("NextToken")
            if not token:
                break
        return RawResult(columns=columns, rows=rows[:max_rows], bytes_scanned=scanned)
