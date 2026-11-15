"""Testes dos executores (DuckDB local e Athena com cliente falso) e da funcao run_query."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from helpers import MONTH
from test_gold import build_silver, run_gold

from cnpj_pipeline.agent.executor import (
    AthenaExecutor,
    DuckDBExecutor,
    QueryFailed,
    RawResult,
    run_query,
    sanitize_error,
    simple_value,
)
from cnpj_pipeline.agent.sql_guard import MAX_ROWS, SqlRejected

SIMPLES = "gold_mart_simples_mei_uf"


# --------------------------------------------------------------------------- #
# DuckDB sobre uma gold real
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def gold_root(tmp_path_factory):
    base = tmp_path_factory.mktemp("exec")
    build_silver(base)
    assert run_gold(base) == 0
    return base / "gold"


@pytest.fixture()
def local(gold_root):
    executor = DuckDBExecutor(gold_root)
    yield executor
    executor.close()


def test_a_guarded_question_runs_on_the_real_gold(local):
    result = run_query(
        f"SELECT uf, empresas_ativas FROM {SIMPLES} WHERE NOT is_organizacao_politica ORDER BY uf",
        executor=local,
        month=MONTH,
    )
    assert result.columns == ["uf", "empresas_ativas"]
    assert result.rows
    assert all((r[0] is None or isinstance(r[0], str)) and isinstance(r[1], int) for r in result.rows)
    assert {r[0] for r in result.rows} >= {"SP", "MG"}
    assert result.tables == (SIMPLES,)
    assert "reference_month = '2026-10'" in result.sql
    assert result.may_be_truncated is False
    assert result.elapsed_s >= 0


def test_rejected_sql_never_reaches_the_engine(local):
    with pytest.raises(SqlRejected):
        run_query("SELECT * FROM gold_fato_estabelecimento", executor=local, month=MONTH)
    with pytest.raises(SqlRejected):
        run_query(f"DROP TABLE {SIMPLES}", executor=local, month=MONTH)


def test_the_limit_is_enforced_and_reported(local):
    result = run_query(f"SELECT uf FROM {SIMPLES}", executor=local, month=MONTH, max_rows=1)
    assert len(result.rows) == 1
    assert result.may_be_truncated is True


def test_reaching_a_limit_the_query_chose_is_not_truncation(local):
    result = run_query(f"SELECT uf FROM {SIMPLES} ORDER BY uf LIMIT 2", executor=local, month=MONTH)
    assert len(result.rows) == 2
    assert result.may_be_truncated is False


def test_a_limit_above_the_cap_is_still_reported_as_truncation(local):
    result = run_query(f"SELECT uf FROM {SIMPLES} LIMIT 1000", executor=local, month=MONTH, max_rows=2)
    assert len(result.rows) == 2
    assert result.may_be_truncated is True


def test_engine_errors_become_a_safe_message(local):
    with pytest.raises(QueryFailed) as err:
        run_query(f"SELECT coluna_que_nao_existe FROM {SIMPLES}", executor=local, month=MONTH)
    assert "coluna_que_nao_existe" in str(err.value)  # o modelo precisa disso para se corrigir


def test_the_local_connection_is_locked_to_the_gold_folder(local, tmp_path):
    outside = tmp_path / "fora.csv"
    outside.write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(duckdb.Error):
        local.con.execute(f"SELECT * FROM read_csv('{outside.as_posix()}')")
    with pytest.raises(duckdb.Error):
        local.con.execute("SET enable_external_access = true")
    with pytest.raises(duckdb.Error):  # a configuracao inteira fica travada, nao so o acesso a arquivos
        local.con.execute("SET memory_limit = '1MB'")


def test_a_slow_query_is_cancelled_by_the_timeout(gold_root):
    executor = DuckDBExecutor(gold_root, timeout_s=0.5)
    try:
        with pytest.raises(QueryFailed, match="tempo máximo"):
            executor.run("SELECT count(*) FROM range(100000000000) a, range(1000000) b", max_rows=10)
        # a conexao continua utilizavel depois do cancelamento
        assert executor.run("SELECT 1 AS x", max_rows=10).rows == [[1]]
    finally:
        executor.close()


# --------------------------------------------------------------------------- #
# Athena com cliente falso
# --------------------------------------------------------------------------- #


class FakeAthena:
    def __init__(self, *, states=("SUCCEEDED",), pages=None, reason="", scanned=1234):
        self.states = list(states)
        self.pages = pages or [
            {
                "ResultSet": {
                    "ResultSetMetadata": {
                        "ColumnInfo": [
                            {"Name": "uf", "Type": "varchar"},
                            {"Name": "n", "Type": "bigint"},
                            {"Name": "pct", "Type": "double"},
                            {"Name": "ok", "Type": "boolean"},
                            {"Name": "total", "Type": "decimal(38,2)"},
                        ]
                    },
                    "Rows": [
                        {"Data": [{"VarCharValue": h} for h in ("uf", "n", "pct", "ok", "total")]},
                        {"Data": [{"VarCharValue": v} for v in ("SP", "10", "75.5", "true", "1234.50")]},
                        {"Data": [{"VarCharValue": "MG"}, {"VarCharValue": "5"}, {}, {}, {}]},
                    ],
                }
            }
        ]
        self.reason = reason
        self.scanned = scanned
        self.started: list[dict] = []
        self.stopped: list[str] = []
        self.result_calls: list[dict] = []

    def start_query_execution(self, **kwargs):
        self.started.append(kwargs)
        return {"QueryExecutionId": "q1"}

    def get_query_execution(self, QueryExecutionId):  # noqa: N803
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        status = {"State": state}
        if self.reason:
            status["StateChangeReason"] = self.reason
        return {"QueryExecution": {"Status": status, "Statistics": {"DataScannedInBytes": self.scanned}}}

    def stop_query_execution(self, QueryExecutionId):  # noqa: N803
        self.stopped.append(QueryExecutionId)

    def get_query_results(self, **kwargs):
        self.result_calls.append(kwargs)
        return self.pages[len(self.result_calls) - 1]


def athena(client, **kwargs):
    return AthenaExecutor(client, database="cnpj", workgroup="cnpj-pipeline", sleep=lambda s: None, **kwargs)


def test_athena_runs_in_the_project_workgroup_and_casts_values():
    client = FakeAthena()
    result = run_query(f"SELECT uf FROM {SIMPLES}", executor=athena(client), month=MONTH)
    sent = client.started[0]
    assert sent["WorkGroup"] == "cnpj-pipeline"
    assert sent["QueryExecutionContext"] == {"Database": "cnpj"}
    assert "reference_month = '2026-10'" in sent["QueryString"]
    assert result.columns == ["uf", "n", "pct", "ok", "total"]
    assert result.rows == [["SP", 10, 75.5, True, "1234.50"], ["MG", 5, None, None, None]]
    assert result.bytes_scanned == 1234


def test_athena_uses_the_trino_dialect():
    assert athena(FakeAthena()).dialect == "trino"
    assert DuckDBExecutor.dialect == "duckdb"


def test_athena_waits_for_the_query_to_finish():
    client = FakeAthena(states=("QUEUED", "RUNNING", "SUCCEEDED"))
    assert athena(client).run("SELECT 1", max_rows=10).rows


def test_athena_failure_becomes_a_safe_message():
    client = FakeAthena(states=("FAILED",), reason="COLUMN_NOT_FOUND: Column 'x' cannot be resolved")
    with pytest.raises(QueryFailed, match="COLUMN_NOT_FOUND"):
        athena(client).run("SELECT x", max_rows=10)


def test_athena_failure_message_hides_paths_arns_and_account():
    reason = (
        "HIVE_CANNOT_OPEN_SPLIT s3://cnpj-pipeline-igor-2026/gold/x.parquet arn:aws:iam::123456789012:role/r"
    )
    client = FakeAthena(states=("FAILED",), reason=reason)
    with pytest.raises(QueryFailed) as err:
        athena(client).run("SELECT 1", max_rows=10)
    message = str(err.value)
    assert "s3://" not in message and "arn:aws" not in message and "123456789012" not in message
    assert "cnpj-pipeline-igor-2026" not in message


def test_athena_timeout_cancels_the_query():
    times = iter([0.0, 1.0, 2.0, 99.0, 100.0, 101.0])
    client = FakeAthena(states=("RUNNING",))
    executor = AthenaExecutor(
        client,
        database="cnpj",
        workgroup="cnpj-pipeline",
        timeout_s=5,
        sleep=lambda s: None,
        clock=lambda: next(times),
    )
    with pytest.raises(QueryFailed, match="tempo máximo"):
        executor.run("SELECT 1", max_rows=10)
    assert client.stopped == ["q1"]


def test_athena_client_errors_do_not_leak_details():
    class Broken(FakeAthena):
        def start_query_execution(self, **kwargs):
            raise RuntimeError("AccessDenied for arn:aws:iam::123456789012:user/x")

    with pytest.raises(QueryFailed) as err:
        athena(Broken()).run("SELECT 1", max_rows=10)
    assert "arn:aws" not in str(err.value) and "123456789012" not in str(err.value)


def test_athena_pagination_stops_at_the_row_limit():
    def page(first, count, token):
        rows = [{"Data": [{"VarCharValue": "uf"}]}] if first else []
        rows += [{"Data": [{"VarCharValue": f"r{i}"}]} for i in range(count)]
        body = {"ResultSet": {"Rows": rows}}
        if first:
            body["ResultSet"]["ResultSetMetadata"] = {"ColumnInfo": [{"Name": "uf", "Type": "varchar"}]}
        if token:
            body["NextToken"] = token
        return body

    client = FakeAthena(pages=[page(True, 3, "t1"), page(False, 3, "t2"), page(False, 3, None)])
    raw = athena(client).run("SELECT uf", max_rows=5)
    assert len(raw.rows) == 5
    assert len(client.result_calls) == 2  # nao pede mais paginas do que precisa


# --------------------------------------------------------------------------- #
# Funcoes auxiliares
# --------------------------------------------------------------------------- #


def test_simple_value_keeps_precision_and_json_friendly_types():
    assert simple_value(None) is None
    assert simple_value(True) is True
    assert simple_value(3) == 3
    assert simple_value(1.5) == 1.5
    assert simple_value(Decimal("12345678901234567890.12")) == "12345678901234567890.12"
    assert simple_value(date(2026, 10, 1)) == "2026-10-01"
    assert len(simple_value("x" * 1000)) == 200


def test_sanitize_error_cleans_and_truncates():
    text = sanitize_error("falhou em C:\\Users\\Igor\\data\\x.parquet e /home/igor/data/y " + "z" * 1000)
    assert "Users" not in text and "/home/" not in text
    assert len(text) <= 400


def test_run_query_passes_the_row_limit_to_the_executor():
    seen = {}

    class Spy:
        dialect = "trino"

        def run(self, sql, *, max_rows):
            seen["max_rows"] = max_rows
            seen["sql"] = sql
            return RawResult(columns=["uf"], rows=[["SP"]])

    run_query(f"SELECT uf FROM {SIMPLES} LIMIT 100000", executor=Spy(), month=MONTH)
    assert seen["max_rows"] == MAX_ROWS
    assert seen["sql"].endswith(f"LIMIT {MAX_ROWS}")


def test_the_agent_package_does_not_need_duckdb_to_be_imported():
    # o agente publicado (funcao na nuvem) usa Athena e nao deve carregar o DuckDB
    code = (
        "import sys; sys.modules['duckdb'] = None\n"
        "from cnpj_pipeline.agent import context, executor, sql_guard\n"
        "r = sql_guard.guard('SELECT uf FROM gold_mart_simples_mei_uf', month='2026-10')\n"
        "assert r.limit == 200\n"
        "assert executor.AthenaExecutor.dialect == 'trino'\n"
    )
    src = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": src}
    )
    assert result.returncode == 0, result.stderr
