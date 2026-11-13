from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from helpers import MONTH, write_bronze

from cnpj_pipeline.profiling import (
    check_relations,
    findings_for_table,
    main,
    profile_table,
    render_table_markdown,
    run,
)


def profile(bronze: Path, table: str, sample_percent: float = 100.0) -> dict:
    con = duckdb.connect()
    try:
        return profile_table(con, bronze, table, MONTH, sample_percent=sample_percent)
    finally:
        con.close()


EMPRESAS = [
    {
        "cnpj_basico": "00000001",
        "razao_social": "ALFA COMERCIO LTDA",
        "natureza_juridica": "2062",
        "capital_social": "1000,50",
        "porte_empresa": "01",
    },
    {
        "cnpj_basico": "00000002",
        "razao_social": " BETA SERVICOS  ",
        "natureza_juridica": "2062",
        "capital_social": "0,00",
        "porte_empresa": "03",
    },
    {
        "cnpj_basico": "00000003",
        "razao_social": "GAMA\tTAB",
        "natureza_juridica": "2305",
        "capital_social": "12.50",
        "porte_empresa": "01",
    },
    {
        "cnpj_basico": "00000003",
        "razao_social": "GAMA DUPLICADA",
        "natureza_juridica": None,
        "capital_social": "5,00",
        "porte_empresa": "05",
    },
]


def test_basic_column_metrics(tmp_path):
    write_bronze(tmp_path, "Empresas", EMPRESAS)

    result = profile(tmp_path, "Empresas")

    assert result["rows_profiled"] == 4
    assert result["files"] == 1
    col = result["columns"]["natureza_juridica"]
    assert col["non_null"] == 3
    assert col["nulls"] == 1
    assert col["null_pct"] == 25.0
    assert col["min_length"] == 4 and col["max_length"] == 4
    assert result["columns"]["cnpj_basico"]["distinct_approx"] == 3
    assert result["columns"]["ente_federativo_responsavel"]["non_null"] == 0


def test_text_hygiene_metrics(tmp_path):
    write_bronze(tmp_path, "Empresas", EMPRESAS)

    col = profile(tmp_path, "Empresas")["columns"]["razao_social"]

    assert col["edge_whitespace"] == 1  # " BETA SERVICOS  "
    assert col["control_chars"] == 1  # tab em "GAMA\tTAB"
    assert col["replacement_chars"] == 0


def test_replacement_character_is_detected(tmp_path):
    rows = [{"cnpj_basico": "1", "razao_social": "NOME COM � INVALIDO"}]
    write_bronze(tmp_path, "Empresas", rows)

    assert profile(tmp_path, "Empresas")["columns"]["razao_social"]["replacement_chars"] == 1


def test_decimal_comma_validation(tmp_path):
    write_bronze(tmp_path, "Empresas", EMPRESAS)

    dec = profile(tmp_path, "Empresas")["columns"]["capital_social"]["decimal_comma"]

    assert dec["matching"] == 3
    assert dec["not_matching"] == 1  # "12.50" usa ponto
    assert dec["with_dot"] == 1
    assert dec["max_value"] == 1000.5


def test_date_columns_valid_sentinel_and_invalid(tmp_path):
    rows = [
        {"cnpj_basico": "1", "data_inicio_atividade": "20200131"},
        {"cnpj_basico": "2", "data_inicio_atividade": "19990615"},
        {"cnpj_basico": "3", "data_inicio_atividade": "0"},
        {"cnpj_basico": "4", "data_inicio_atividade": "00000000"},
        {"cnpj_basico": "5", "data_inicio_atividade": "20201340"},  # mes e dia inexistentes
        {"cnpj_basico": "6", "data_inicio_atividade": "31/01/2020"},  # formato errado
        {"cnpj_basico": "7", "data_inicio_atividade": None},
    ]
    write_bronze(tmp_path, "Estabelecimentos", rows)

    col = profile(tmp_path, "Estabelecimentos")["columns"]["data_inicio_atividade"]

    assert col["date"] == {
        "valid": 2,
        "sentinel_zero": 2,
        "invalid": 2,
        "min_valid": "1999-06-15",
        "max_valid": "2020-01-31",
    }
    assert {s["value"]: s["count"] for s in col["sentinels"]} == {"0": 1, "00000000": 1}


def test_patterns_are_masked_and_only_for_short_columns(tmp_path):
    rows = [{"cnpj_basico": f"{i:08d}", "razao_social": f"EMPRESA {i}"} for i in range(5)]
    rows.append({"cnpj_basico": "ABCD1234", "razao_social": "X" * 40})
    write_bronze(tmp_path, "Empresas", rows)

    cols = profile(tmp_path, "Empresas")["columns"]

    patterns = {p["pattern"]: p["count"] for p in cols["cnpj_basico"]["patterns"]}
    assert patterns == {"99999999": 5, "AAAA9999": 1}
    assert "patterns" not in cols["razao_social"]  # texto longo: sem padroes


def test_literal_values_only_for_allowed_categorical_columns(tmp_path):
    write_bronze(tmp_path, "Empresas", EMPRESAS)

    cols = profile(tmp_path, "Empresas")["columns"]

    assert [v["value"] for v in cols["porte_empresa"]["top_values"]][0] == "01"
    assert "top_values" not in cols["razao_social"]
    assert "top_values" not in cols["cnpj_basico"]


def test_duplicate_business_key_is_reported(tmp_path):
    write_bronze(tmp_path, "Empresas", EMPRESAS)

    key = profile(tmp_path, "Empresas")["key"]

    assert key == {"columns": ["cnpj_basico"], "duplicated_keys": 1, "rows_in_duplicated_keys": 2}


def test_composite_key_for_estabelecimentos(tmp_path):
    rows = [
        {"cnpj_basico": "1", "cnpj_ordem": "0001", "cnpj_dv": "01"},
        {"cnpj_basico": "1", "cnpj_ordem": "0002", "cnpj_dv": "02"},
        {"cnpj_basico": "1", "cnpj_ordem": "0001", "cnpj_dv": "01"},
    ]
    write_bronze(tmp_path, "Estabelecimentos", rows)

    key = profile(tmp_path, "Estabelecimentos")["key"]

    assert key["duplicated_keys"] == 1
    assert key["rows_in_duplicated_keys"] == 2


def test_multiple_parquet_files_are_combined(tmp_path):
    write_bronze(tmp_path, "Empresas", EMPRESAS[:2], name="Empresas0")
    write_bronze(tmp_path, "Empresas", EMPRESAS[2:], name="Empresas1")

    result = profile(tmp_path, "Empresas")

    assert result["files"] == 2
    assert result["rows_profiled"] == 4


def test_sampling_reduces_rows(tmp_path):
    rows = [{"cnpj_basico": f"{i:08d}"} for i in range(2000)]
    write_bronze(tmp_path, "Empresas", rows)

    result = profile(tmp_path, "Empresas", sample_percent=10)

    assert 100 < result["rows_profiled"] < 400
    assert result["sample_percent"] == 10


def test_referential_orphans(tmp_path):
    write_bronze(tmp_path, "Empresas", [{"cnpj_basico": "1"}, {"cnpj_basico": "2"}])
    write_bronze(
        tmp_path,
        "Estabelecimentos",
        [
            {"cnpj_basico": "1", "cnpj_ordem": "0001", "cnpj_dv": "01"},
            {"cnpj_basico": "2", "cnpj_ordem": "0001", "cnpj_dv": "02"},
            {"cnpj_basico": "9", "cnpj_ordem": "0001", "cnpj_dv": "03"},  # orfa
            {"cnpj_basico": None, "cnpj_ordem": "0001", "cnpj_dv": "04"},  # nula: nao conta
        ],
    )
    con = duckdb.connect()

    findings = check_relations(con, tmp_path, MONTH)

    assert len(findings) == 1  # so ha o par Estabelecimentos -> Empresas
    f = findings[0]
    assert (f["child"], f["parent"]) == ("Estabelecimentos", "Empresas")
    assert f["checked_rows"] == 3
    assert f["orphan_rows"] == 1
    assert f["orphan_pct"] == pytest.approx(33.3333, abs=0.001)


def test_findings_summarize_problems(tmp_path):
    write_bronze(tmp_path, "Empresas", EMPRESAS)

    text = "\n".join(findings_for_table(profile(tmp_path, "Empresas")))

    assert "`razao_social`: 1 valor(es) com espaços nas bordas" in text
    assert "`razao_social`: 1 valor(es) com caracteres de controle" in text
    assert "`capital_social`: 1 valor(es) fora do padrão" in text
    assert "chave `cnpj_basico`: 1 chave(s) duplicada(s)" in text
    assert "`ente_federativo_responsavel`: todos os valores são nulos" in text


def test_markdown_report_does_not_expose_names(tmp_path):
    write_bronze(tmp_path, "Empresas", EMPRESAS)

    markdown = render_table_markdown(profile(tmp_path, "Empresas"))

    assert "# Profiling: Empresas (2026-10)" in markdown
    assert "ALFA COMERCIO" not in markdown
    assert "BETA SERVICOS" not in markdown
    assert "GAMA" not in markdown


def test_run_writes_json_and_markdown_reports(tmp_path):
    bronze, out = tmp_path / "bronze", tmp_path / "out"
    write_bronze(bronze, "Empresas", EMPRESAS)
    write_bronze(bronze, "Cnaes", [{"codigo": "0111301", "descricao": "Cultivo de arroz"}])

    code = run(
        MONTH,
        ["Empresas", "Cnaes", "Socios"],  # Socios sem Parquet: aviso
        bronze_dir=bronze,
        out_dir=out,
        memory_limit="1GB",
        threads=2,
        sample_percent=100.0,
        skip_referential=False,
    )

    assert code == 0
    for name in (
        "empresas.json",
        "empresas.md",
        "cnaes.json",
        "cnaes.md",
        "referential.json",
        "referential.md",
    ):
        assert (out / MONTH / name).exists(), name
    data = json.loads((out / MONTH / "empresas.json").read_text(encoding="utf-8"))
    assert data["rows_profiled"] == 4


def test_run_fails_when_bronze_is_empty(tmp_path):
    code = run(
        MONTH,
        ["Empresas"],
        bronze_dir=tmp_path / "bronze",
        out_dir=tmp_path / "out",
        memory_limit="1GB",
        threads=2,
        sample_percent=100.0,
        skip_referential=True,
    )
    assert code == 1


def test_cli_validates_arguments(capsys):
    assert main(["--month", "outubro"]) == 2
    assert main(["--month", MONTH, "--sample-percent", "0"]) == 2
    assert main(["--month", MONTH, "--tables", "inexistente"]) == 2
    assert "invalido" in capsys.readouterr().err
