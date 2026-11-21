"""Testes do experimento Glue/Spark (experiments/glue_spark).

A traducao do SQL e a entrada sao testadas sempre. A comparacao com o DuckDB roda o Spark de verdade, em
modo local, sobre os dados sinteticos do projeto, e so roda onde ha pyspark e Java (por isso e pulada
no CI): e ela que garante que o SQL da gold da o mesmo resultado nos dois motores.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import duckdb
import pytest

EXPERIMENT_DIR = Path(__file__).resolve().parents[1] / "experiments" / "glue_spark"
sys.path.insert(0, str(EXPERIMENT_DIR))

import make_input  # noqa: E402
import spark_gold  # noqa: E402

from cnpj_pipeline import gold_models as gm  # noqa: E402

MONTH = "2000-01"


def test_strftime_and_varchar_are_translated_for_spark():
    sql = "SELECT strftime(b.data_inicio_atividade, '%Y-%m') AS m, CAST('x' AS VARCHAR) AS v"
    assert spark_gold.to_spark(sql) == (
        "SELECT date_format(b.data_inicio_atividade, 'yyyy-MM') AS m, CAST('x' AS STRING) AS v"
    )


def test_a_strftime_format_that_is_not_known_is_an_error_and_not_silently_wrong():
    with pytest.raises(ValueError, match="strftime"):
        spark_gold.to_spark("SELECT strftime(d, '%d/%m/%Y')")


def test_the_translated_sql_of_every_model_has_no_duckdb_only_syntax():
    for name in spark_gold.MODEL_NAMES:
        sql = spark_gold.to_spark(gm.MODELS_BY_NAME[name].sql(MONTH, "2000-01-01 00:00:00"))
        assert "strftime" not in sql and "VARCHAR" not in sql, name


def test_the_models_built_with_spark_exclude_the_partners_mart_and_follow_the_dependency_order():
    assert "mart_socios_qualificacao" not in spark_gold.MODEL_NAMES
    built: set[str] = set()
    for name in spark_gold.MODEL_NAMES:
        model = gm.MODELS_BY_NAME[name]
        assert set(model.depends) <= built, f"{name} depende de modelo ainda nao construido"
        assert {t.lower() for t in model.sources} <= {t.lower() for t in spark_gold.INPUT_COLUMNS}, name
        built.add(name)


def test_no_input_column_holds_a_name_address_or_contact():
    forbidden = {"razao_social", "nome_fantasia", "logradouro", "correio_eletronico", "telefone_1", "cep"}
    for columns in spark_gold.INPUT_COLUMNS.values():
        assert not forbidden & set(columns)


@pytest.fixture(scope="module")
def sample_layers(tmp_path_factory):
    """Silver e gold sinteticas, geradas pelo proprio pipeline do projeto."""
    data = tmp_path_factory.mktemp("data")
    env = {**os.environ, "CNPJ_DATA_DIR": str(data)}
    subprocess.run(
        [sys.executable, "-m", "cnpj_pipeline.orchestration", "--months", MONTH, "--mode", "sample"]
        + ["--skip-site"],
        env=env,
        check=True,
        capture_output=True,
        timeout=300,
    )
    return data / "sample"


def test_the_input_has_only_the_columns_the_sql_uses(sample_layers, tmp_path):
    rows = make_input.build_input(sample_layers / "silver", tmp_path, MONTH)
    assert set(rows) == set(spark_gold.INPUT_COLUMNS)
    assert rows["Estabelecimentos"] > 0
    for table, columns in spark_gold.INPUT_COLUMNS.items():
        path = tmp_path / table.lower() / f"reference_month={MONTH}" / f"{table.lower()}.parquet"
        found = [
            r[0]
            for r in duckdb.sql(
                f"DESCRIBE SELECT * FROM read_parquet('{path.as_posix()}', hive_partitioning=false)"
            ).fetchall()
        ]
        assert tuple(found) == columns


def test_the_input_script_reports_a_missing_silver_with_exit_code_two(tmp_path, capsys):
    code = make_input.main(
        ["--month", MONTH, "--silver-dir", str(tmp_path), "--out-dir", str(tmp_path / "o")]
    )
    assert code == 2
    assert "nao encontrada" in capsys.readouterr().err


def test_the_summary_goes_to_s3_when_the_output_is_on_s3(monkeypatch):
    puts = []

    class FakeS3:
        def put_object(self, **kwargs):
            puts.append(kwargs)

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda service: FakeS3()))
    target = spark_gold.write_summary(
        "s3://bucket/experiments/spark/output/", {"mes": MONTH}, "2026-11-21 21:00:54"
    )
    assert target == "s3://bucket/experiments/spark/output/_resumo_20261121T210054.json"
    assert puts[0]["Bucket"] == "bucket"
    assert puts[0]["Key"] == "experiments/spark/output/_resumo_20261121T210054.json"
    assert json.loads(puts[0]["Body"])["mes"] == MONTH


def test_the_summary_is_written_to_a_local_folder_too(tmp_path):
    target = spark_gold.write_summary(str(tmp_path), {"mes": MONTH}, "2026-11-21 21:00:54")
    assert json.loads(Path(target).read_text(encoding="utf-8")) == {"mes": MONTH}


spark_available = shutil.which("java") is not None
try:
    import pyspark  # noqa: F401
except ImportError:
    spark_available = False


@pytest.fixture(scope="module")
def spark_run(sample_layers, tmp_path_factory):
    """Roda o job em Spark local sobre os dados sinteticos e devolve (sessao, pastas)."""
    from pyspark.sql import SparkSession

    base = tmp_path_factory.mktemp("spark")
    input_dir, output_dir = base / "input", base / "output"
    make_input.build_input(sample_layers / "silver", input_dir, MONTH)
    code = spark_gold.main(
        ["--local", "--month", MONTH, "--input-dir", str(input_dir), "--output-dir", str(output_dir)]
    )
    assert code == 0
    spark = SparkSession.builder.master("local[2]").getOrCreate()
    yield spark, output_dir, sample_layers / "gold"
    spark.stop()


@pytest.mark.skipif(not spark_available, reason="precisa de pyspark e Java")
def test_spark_and_duckdb_build_the_same_gold(spark_run):
    spark, output_dir, gold_dir = spark_run
    report = spark_gold.compare(spark, str(output_dir), str(gold_dir), MONTH)
    assert [entry["modelo"] for entry in report] == list(spark_gold.MODEL_NAMES)
    assert all(entry["igual"] for entry in report), [e for e in report if not e["igual"]]
    assert all(entry["linhas_spark"] == entry["linhas_duckdb"] > 0 for entry in report)


@pytest.mark.skipif(not spark_available, reason="precisa de pyspark e Java")
def test_the_comparison_detects_a_wrong_value(spark_run, tmp_path):
    spark, output_dir, gold_dir = spark_run
    altered = tmp_path / "gold"
    shutil.copytree(gold_dir, altered)
    fato = altered / "fato_estabelecimento" / f"reference_month={MONTH}" / "fato_estabelecimento.parquet"
    original = fato.with_name("original.parquet")
    fato.rename(original)
    duckdb.execute(
        "COPY (SELECT * REPLACE ("
        "CASE WHEN cnpj = (SELECT min(cnpj) FROM read_parquet(?)) THEN 'ZZ' ELSE uf END AS uf) "
        "FROM read_parquet(?)) TO ? (FORMAT parquet)",
        [original.as_posix(), original.as_posix(), fato.as_posix()],
    )
    original.unlink()
    report = spark_gold.compare(spark, str(output_dir), str(altered), MONTH)
    by_name = {entry["modelo"]: entry for entry in report}
    assert by_name["fato_estabelecimento"]["igual"] is False
    assert by_name["dim_porte"]["igual"] is True


@pytest.mark.skipif(not spark_available, reason="precisa de pyspark e Java")
def test_the_median_tolerance_does_not_hide_a_larger_difference(spark_run, tmp_path):
    spark, output_dir, gold_dir = spark_run
    altered = tmp_path / "gold"
    shutil.copytree(gold_dir, altered)
    mart = (
        altered
        / "mart_perfil_porte_natureza"
        / f"reference_month={MONTH}"
        / "mart_perfil_porte_natureza.parquet"
    )
    original = mart.with_name("original.parquet")
    mart.rename(original)
    duckdb.execute(
        "COPY (SELECT * REPLACE (capital_social_mediano + 5 AS capital_social_mediano) FROM read_parquet(?)) "
        "TO ? (FORMAT parquet)",
        [original.as_posix(), mart.as_posix()],
    )
    original.unlink()
    report = spark_gold.compare(spark, str(output_dir), str(altered), MONTH)
    entry = next(e for e in report if e["modelo"] == "mart_perfil_porte_natureza")
    assert entry["igual"] is False
    assert entry["capital_social_mediano_diferenca_maxima"] >= 5


@pytest.mark.skipif(not spark_available, reason="precisa de pyspark e Java")
def test_the_run_leaves_a_summary_with_the_time_of_every_model(spark_run):
    _, output_dir, _ = spark_run
    files = list(output_dir.glob("_resumo_*.json"))
    assert len(files) == 1
    summary = json.loads(files[0].read_text(encoding="utf-8"))
    assert summary["mes"] == MONTH and summary["construcao_segundos"] > 0
    assert summary["spark"].startswith("3.5")
    assert [m["modelo"] for m in summary["modelos"]] == list(spark_gold.MODEL_NAMES)
