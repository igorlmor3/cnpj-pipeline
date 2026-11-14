from __future__ import annotations

import json

import duckdb
import pytest
from helpers import MONTH, write_bronze
from test_gold import build_silver, run_gold

from cnpj_pipeline import config
from cnpj_pipeline.cloud import athena
from cnpj_pipeline.gold import gold_path
from cnpj_pipeline.gold_models import MODEL_NAMES
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


# --------------------------------------------------------------------------- #
# Gold
# --------------------------------------------------------------------------- #


@pytest.fixture
def built_gold(tmp_path):
    build_silver(tmp_path)
    assert run_gold(tmp_path) == 0
    return tmp_path


@pytest.mark.parametrize(
    ("duckdb_type", "expected"),
    [
        ("VARCHAR", "string"),
        ("INTEGER", "int"),
        ("BIGINT", "bigint"),
        ("BOOLEAN", "boolean"),
        ("DATE", "date"),
        ("TIMESTAMP", "timestamp"),
        ("DECIMAL(20,2)", "decimal(20,2)"),
        ("DECIMAL(38,2)", "decimal(38,2)"),
    ],
)
def test_athena_type_mapping(duckdb_type, expected):
    assert athena.athena_type(duckdb_type) == expected


def test_athena_type_rejects_unknown_types():
    with pytest.raises(athena.AthenaError, match="STRUCT"):
        athena.athena_type("STRUCT(a INTEGER)")


def test_gold_ddl_matches_the_parquet_the_gold_really_writes(built_gold):
    gold_dir = built_gold / "gold"
    columns = athena.gold_columns(gold_dir, MONTH, list(MODEL_NAMES))

    con = duckdb.connect()
    for model in MODEL_NAMES:
        path = gold_path(gold_dir, model, MONTH).as_posix()
        described = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{path}', hive_partitioning=false)"
        ).fetchall()
        assert [(r[0], athena.athena_type(r[1])) for r in described] == columns[model], model


def test_gold_ddl_uses_gold_names_and_location(built_gold):
    columns = athena.gold_columns(built_gold / "gold", MONTH, ["fato_estabelecimento"])
    ddl = athena.create_table_ddl(
        "fato_estabelecimento",
        bucket="meu-bucket",
        layer="gold",
        columns=columns["fato_estabelecimento"],
    )

    assert "`cnpj`.`gold_fato_estabelecimento`" in ddl
    assert "LOCATION 's3://meu-bucket/gold/fato_estabelecimento/'" in ddl
    assert "`is_ativo` boolean" in ddl and "`ano_inicio_atividade` int" in ddl
    assert "`capital_social` decimal(20,2)" in ddl
    assert "reference_month=${reference_month}/" in ddl


def test_gold_columns_errors_when_manifest_or_model_is_missing(built_gold):
    gold_dir = built_gold / "gold"
    with pytest.raises(athena.AthenaError, match="manifesto da gold"):
        athena.gold_columns(gold_dir, "2000-01", ["dim_porte"])

    manifest = gold_dir / f"_manifest_{MONTH}.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    del data["models"]["dim_porte"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(athena.AthenaError, match="dim_porte"):
        athena.gold_columns(gold_dir, MONTH, ["dim_porte"])


def test_create_gold_tables_runs_one_ddl_per_model(built_gold):
    models = ["dim_porte", "mart_simples_mei_uf"]
    client = FakeAthena(["SUCCEEDED"])
    athena.create_tables(
        client,
        bucket="b",
        database="cnpj",
        workgroup="wg",
        tables=models,
        start_month="2023-01",
        layer="gold",
        columns=athena.gold_columns(built_gold / "gold", MONTH, models),
        sleep=lambda _: None,
    )
    assert [q["QueryString"].split("`")[3] for q in client.started] == [
        "gold_dim_porte",
        "gold_mart_simples_mei_uf",
    ]


def test_replace_drops_each_table_before_creating_it(built_gold):
    models = ["dim_porte", "mart_simples_mei_uf"]
    client = FakeAthena(["SUCCEEDED"])
    athena.create_tables(
        client,
        bucket="b",
        database="cnpj",
        workgroup="wg",
        tables=models,
        start_month="2023-01",
        layer="gold",
        columns=athena.gold_columns(built_gold / "gold", MONTH, models),
        replace=True,
        sleep=lambda _: None,
    )
    sent = [q["QueryString"] for q in client.started]
    assert [q.split(" ")[0] for q in sent] == ["DROP", "CREATE", "DROP", "CREATE"]
    assert sent[0] == "DROP TABLE IF EXISTS `cnpj`.`gold_dim_porte`"
    assert "`gold_mart_simples_mei_uf`" in sent[3]


def test_without_replace_never_drops(built_gold):
    client = FakeAthena(["SUCCEEDED"])
    athena.create_tables(
        client,
        bucket="b",
        database="cnpj",
        workgroup="wg",
        tables=["dim_porte"],
        start_month="2023-01",
        layer="gold",
        columns=athena.gold_columns(built_gold / "gold", MONTH, ["dim_porte"]),
        sleep=lambda _: None,
    )
    assert not any("DROP" in q["QueryString"] for q in client.started)


def test_cli_replace_is_passed_through_and_printed(built_gold, capsys):
    base = ["create-tables", "--layer", "gold", "--bucket", "b", "--gold-dir", str(built_gold / "gold")]
    base += ["--month", MONTH, "--tables", "dim_porte"]

    assert athena.main([*base, "--print-only", "--replace"]) == 0
    out = capsys.readouterr().out
    assert out.index("DROP TABLE IF EXISTS `cnpj`.`gold_dim_porte`") < out.index("CREATE EXTERNAL TABLE")
    assert athena.main([*base, "--print-only"]) == 0
    assert "DROP" not in capsys.readouterr().out

    client = FakeAthena(["SUCCEEDED"])
    assert athena.main([*base, "--replace"], client=client) == 0
    assert [q["QueryString"].split(" ")[0] for q in client.started] == ["DROP", "CREATE"]


def test_cli_gold_print_only_and_validations(built_gold, capsys):
    base = ["create-tables", "--layer", "gold", "--bucket", "b", "--gold-dir", str(built_gold / "gold")]

    assert athena.main([*base, "--month", MONTH, "--print-only", "--tables", "dim_porte"]) == 0
    out = capsys.readouterr().out
    assert "gold_dim_porte" in out and "silver_" not in out

    assert athena.main([*base, "--month", MONTH, "--print-only"]) == 0  # todos os modelos
    assert capsys.readouterr().out.count("CREATE EXTERNAL TABLE") == len(MODEL_NAMES)

    assert athena.main([*base, "--print-only"]) == 2  # falta --month
    assert athena.main([*base, "--month", MONTH, "--print-only", "--tables", "nada"]) == 2
    assert athena.main([*base, "--month", "2000-01", "--print-only"]) == 2  # sem manifesto
