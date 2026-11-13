from __future__ import annotations

import duckdb
import pytest
from helpers import MONTH, write_bronze

from cnpj_pipeline import config
from cnpj_pipeline.cloud import athena
from cnpj_pipeline.silver import run, silver_path
from cnpj_pipeline.silver_rules import KINDS, REQUIRED, RULES

KEY_VALUES = {"key8": "00000001", "key4": "0001", "dv2": "91", "code": "1"}


def minimal_row(table: str) -> dict:
    return {c: KEY_VALUES[RULES[table][c]] for c in REQUIRED[table]}


def test_ddl_matches_the_parquet_the_silver_really_writes(tmp_path):
    for table in config.ALL_TABLES:
        write_bronze(tmp_path / "bronze", table, [minimal_row(table)])
    code = run(
        MONTH,
        list(config.ALL_TABLES),
        bronze_dir=tmp_path / "bronze",
        silver_dir=tmp_path / "silver",
        report_dir=None,
        force=False,
        memory_limit="1GB",
        threads=2,
        max_quarantine_ratio=1.0,
        tmp_root=tmp_path / "tmp",
    )
    assert code == 0

    to_athena = {
        "VARCHAR": "string",
        "DATE": "date",
        "DECIMAL(20,2)": "decimal(20,2)",
        "BOOLEAN": "boolean",
        "VARCHAR[]": "array<string>",
        "TIMESTAMP": "timestamp",
    }
    con = duckdb.connect()
    for table in config.ALL_TABLES:
        path = silver_path(tmp_path / "silver", table, MONTH).as_posix()
        described = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{path}', hive_partitioning=false)"
        ).fetchall()
        actual = [(r[0], to_athena[r[1]]) for r in described]
        assert actual == athena.silver_columns(table), table


def test_every_rule_kind_has_an_athena_type():
    for kind, (duckdb_type, _) in KINDS.items():
        assert duckdb_type in athena.ATHENA_TYPES, kind


def test_ddl_shape_and_quoting():
    ddl = athena.create_table_ddl("Estabelecimentos", bucket="meu-bucket", database="cnpj")

    assert "CREATE EXTERNAL TABLE IF NOT EXISTS `cnpj`.`silver_estabelecimentos`" in ddl
    assert "`_ingested_at` timestamp" in ddl  # nomes com underscore inicial exigem crase
    assert "`cnae_fiscal_secundaria` array<string>" in ddl
    assert "`data_inicio_atividade` date" in ddl
    assert "`cnpj` string" in ddl
    assert "PARTITIONED BY (`reference_month` string)" in ddl
    assert "STORED AS PARQUET" in ddl
    assert "LOCATION 's3://meu-bucket/silver/estabelecimentos/'" in ddl
    assert "'projection.enabled' = 'true'" in ddl
    assert "'projection.reference_month.range' = '2023-01,NOW'" in ddl
    assert "reference_month=${reference_month}/" in ddl


def test_empresas_decimal_and_flags_types():
    empresas = dict(athena.silver_columns("Empresas"))
    simples = dict(athena.silver_columns("Simples"))
    assert empresas["capital_social"] == "decimal(20,2)"
    assert simples["opcao_pelo_simples"] == "boolean"
    assert simples["data_opcao_simples"] == "date"


class FakeAthena:
    def __init__(self, states: list[str], reason: str = "") -> None:
        self.states = list(states)
        self.reason = reason
        self.started: list[dict] = []

    def start_query_execution(self, **kwargs):
        self.started.append(kwargs)
        return {"QueryExecutionId": "q-1"}

    def get_query_execution(self, QueryExecutionId):  # noqa: N803
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"QueryExecution": {"Status": {"State": state, "StateChangeReason": self.reason}}}

    def get_query_results(self, QueryExecutionId, NextToken=None):  # noqa: N803
        if NextToken is None:
            return {"ResultSet": {"Rows": [{"Data": [{"VarCharValue": "n"}]}]}, "NextToken": "p2"}
        return {"ResultSet": {"Rows": [{"Data": [{"VarCharValue": "42"}, {}]}]}}


def test_run_query_polls_until_success():
    client = FakeAthena(["QUEUED", "RUNNING", "SUCCEEDED"])
    sleeps: list[float] = []
    execution = athena.run_query(client, "SELECT 1", database="cnpj", workgroup="wg", sleep=sleeps.append)

    assert execution == "q-1"
    assert len(sleeps) == 2
    assert client.started[0]["WorkGroup"] == "wg"
    assert client.started[0]["QueryExecutionContext"] == {"Database": "cnpj"}


def test_run_query_raises_with_reason_on_failure():
    client = FakeAthena(["RUNNING", "FAILED"], reason="TABLE_NOT_FOUND")
    with pytest.raises(athena.AthenaError, match="TABLE_NOT_FOUND"):
        athena.run_query(client, "SELECT 1", database="cnpj", workgroup="wg", sleep=lambda _: None)


def test_fetch_rows_follows_pagination_and_nulls():
    assert athena.fetch_rows(FakeAthena(["SUCCEEDED"]), "q-1") == [["n"], ["42", None]]


def test_create_tables_runs_one_ddl_per_table():
    client = FakeAthena(["SUCCEEDED"])
    athena.create_tables(
        client,
        bucket="b",
        database="cnpj",
        workgroup="wg",
        tables=["Empresas", "Socios"],
        start_month="2023-01",
        sleep=lambda _: None,
    )
    assert [q["QueryString"].split("`")[3] for q in client.started] == ["silver_empresas", "silver_socios"]


def test_cli_print_only_and_validations(capsys):
    assert athena.main(["create-tables", "--bucket", "b", "--print-only", "--tables", "Cnaes"]) == 0
    assert "silver_cnaes" in capsys.readouterr().out
    assert athena.main(["create-tables", "--print-only", "--bucket", "b", "--tables", "nada"]) == 2


def test_cli_query_prints_result(capsys):
    client = FakeAthena(["SUCCEEDED"])
    assert athena.main(["query", "SELECT 1"], client=client) == 0
    assert capsys.readouterr().out.splitlines() == ["n", "42\t"]
