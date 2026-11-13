from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from helpers import MONTH, write_bronze

from cnpj_pipeline import config
from cnpj_pipeline.schemas import columns_for
from cnpj_pipeline.silver import (
    main,
    quarantine_path,
    render_report,
    run,
    silver_path,
)
from cnpj_pipeline.silver_rules import DEDUP_KEYS, KINDS, REQUIRED, RULES, TEXT_LIKE


def run_silver(tmp_path: Path, tables: list[str], *, force: bool = False, ratio: float = 0.5) -> int:
    return run(
        MONTH,
        tables,
        bronze_dir=tmp_path / "bronze",
        silver_dir=tmp_path / "silver",
        report_dir=None,
        force=force,
        memory_limit="1GB",
        threads=2,
        max_quarantine_ratio=ratio,
        tmp_root=tmp_path / "tmp",
    )


def read(path: Path) -> list[dict]:
    con = duckdb.connect()
    try:
        cur = con.execute(f"SELECT * FROM read_parquet('{path.as_posix()}', hive_partitioning=false)")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def silver_rows(tmp_path: Path, table: str) -> list[dict]:
    return read(silver_path(tmp_path / "silver", table, MONTH))


def quarantine_rows(tmp_path: Path, table: str) -> list[dict]:
    return read(quarantine_path(tmp_path / "silver", table, MONTH))


def column_types(path: Path) -> dict[str, str]:
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{path.as_posix()}', hive_partitioning=false)"
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Consistencia das regras
# --------------------------------------------------------------------------- #


def test_rules_cover_exactly_the_schema_columns():
    assert set(RULES) == set(config.ALL_TABLES)
    for table in config.ALL_TABLES:
        assert list(RULES[table]) == list(columns_for(table)), table


def test_rules_use_known_kinds_and_keys_exist():
    for table, rules in RULES.items():
        assert set(rules.values()) <= set(KINDS), table
        assert set(REQUIRED[table]) <= set(rules), table
        assert set(DEDUP_KEYS.get(table, ())) <= set(rules), table
    assert TEXT_LIKE <= set(KINDS)


# --------------------------------------------------------------------------- #
# Limpeza e tipagem
# --------------------------------------------------------------------------- #


def test_text_cleaning_and_decimal_and_types(tmp_path):
    write_bronze(
        tmp_path / "bronze",
        "Empresas",
        [
            {
                "cnpj_basico": "00000001",
                "razao_social": "  ALFA \t  COMERCIO   LTDA ",
                "natureza_juridica": " 2062 ",
                "capital_social": "1000,50",
                "porte_empresa": "01",
                "ente_federativo_responsavel": "   ",
            },
            {"cnpj_basico": "00000002", "razao_social": "BETA", "capital_social": "12.50"},
            {"cnpj_basico": "00000003", "razao_social": "GAMA", "capital_social": "abc"},
        ],
    )

    assert run_silver(tmp_path, ["Empresas"]) == 0
    rows = {r["cnpj_basico"]: r for r in silver_rows(tmp_path, "Empresas")}

    assert rows["00000001"]["razao_social"] == "ALFA COMERCIO LTDA"
    assert rows["00000001"]["natureza_juridica"] == "2062"
    assert rows["00000001"]["capital_social"] == Decimal("1000.50")
    assert rows["00000001"]["ente_federativo_responsavel"] is None
    assert rows["00000002"]["capital_social"] == Decimal("12.50")  # ponto tambem aceito
    assert rows["00000003"]["capital_social"] is None  # invalido vira NULL
    types = column_types(silver_path(tmp_path / "silver", "Empresas", MONTH))
    assert types["capital_social"] == "DECIMAL(20,2)"
    assert types["razao_social"] == "VARCHAR"
    assert types["_silver_processed_at"] == "TIMESTAMP"
    assert {"_source_file", "_reference_month", "_ingested_at"} <= set(types)


def test_codes_keep_leading_zeros(tmp_path):
    write_bronze(
        tmp_path / "bronze",
        "Empresas",
        [{"cnpj_basico": "00000010", "natureza_juridica": "0001", "porte_empresa": "05"}],
    )
    assert run_silver(tmp_path, ["Empresas"]) == 0
    (row,) = silver_rows(tmp_path, "Empresas")
    assert row["cnpj_basico"] == "00000010"
    assert row["natureza_juridica"] == "0001"


def test_dates_valid_zero_and_impossible(tmp_path):
    base = {"cnpj_ordem": "0001", "cnpj_dv": "91"}
    dates = {
        "00000001": "20200229",  # bissexto valido
        "00000002": "00000000",  # sentinela
        "00000003": "20230231",  # inexistente
        "00000004": "2023-01-05",  # formato errado
        "00000005": "",  # vazio
    }
    write_bronze(
        tmp_path / "bronze",
        "Estabelecimentos",
        [{**base, "cnpj_basico": k, "data_inicio_atividade": v or None} for k, v in dates.items()],
    )
    assert run_silver(tmp_path, ["Estabelecimentos"], ratio=1.0) == 0
    got = {r["cnpj_basico"]: r["data_inicio_atividade"] for r in silver_rows(tmp_path, "Estabelecimentos")}

    assert got["00000001"] == date(2020, 2, 29)
    assert all(got[k] is None for k in ("00000002", "00000003", "00000004", "00000005"))
    types = column_types(silver_path(tmp_path / "silver", "Estabelecimentos", MONTH))
    assert types["data_inicio_atividade"] == "DATE"


def test_estabelecimentos_cnpj_uf_email_and_list(tmp_path):
    write_bronze(
        tmp_path / "bronze",
        "Estabelecimentos",
        [
            {
                "cnpj_basico": "00000001",
                "cnpj_ordem": "0001",
                "cnpj_dv": "91",
                "uf": " sp ",
                "correio_eletronico": " Contato@EXEMPLO.test ",
                "cnae_fiscal_secundaria": ",1111111, 2222222,, 3333333 ,",
            },
            {"cnpj_basico": "00000002", "cnpj_ordem": "0002", "cnpj_dv": "10", "uf": "XX"},
            {
                "cnpj_basico": "00000003",
                "cnpj_ordem": "0003",
                "cnpj_dv": "20",
                "cnae_fiscal_secundaria": " , ",
            },
        ],
    )
    assert run_silver(tmp_path, ["Estabelecimentos"], ratio=1.0) == 0
    rows = {r["cnpj_basico"]: r for r in silver_rows(tmp_path, "Estabelecimentos")}

    assert rows["00000001"]["cnpj"] == "00000001000191"
    assert rows["00000001"]["uf"] == "SP"
    assert rows["00000001"]["correio_eletronico"] == "contato@exemplo.test"
    assert rows["00000001"]["cnae_fiscal_secundaria"] == ["1111111", "2222222", "3333333"]
    assert rows["00000002"]["uf"] is None  # UF inexistente
    assert rows["00000003"]["cnae_fiscal_secundaria"] is None  # so separadores


def test_simples_flags(tmp_path):
    write_bronze(
        tmp_path / "bronze",
        "Simples",
        [
            {"cnpj_basico": "00000001", "opcao_pelo_simples": "S", "opcao_pelo_mei": "n"},
            {"cnpj_basico": "00000002", "opcao_pelo_simples": "X", "data_opcao_simples": "20210101"},
        ],
    )
    assert run_silver(tmp_path, ["Simples"], ratio=1.0) == 0
    rows = {r["cnpj_basico"]: r for r in silver_rows(tmp_path, "Simples")}

    assert rows["00000001"]["opcao_pelo_simples"] is True
    assert rows["00000001"]["opcao_pelo_mei"] is False
    assert rows["00000002"]["opcao_pelo_simples"] is None
    assert rows["00000002"]["data_opcao_simples"] == date(2021, 1, 1)


def test_alphanumeric_keys_are_accepted(tmp_path):
    write_bronze(tmp_path / "bronze", "Empresas", [{"cnpj_basico": "ab12cd34"}, {"cnpj_basico": "AB-12345"}])
    assert run_silver(tmp_path, ["Empresas"], ratio=1.0) == 0
    assert [r["cnpj_basico"] for r in silver_rows(tmp_path, "Empresas")] == ["AB12CD34"]
    (bad,) = quarantine_rows(tmp_path, "Empresas")
    assert bad["_reject_reason"] == "invalid_key:cnpj_basico"


# --------------------------------------------------------------------------- #
# Quarentena e deduplicacao
# --------------------------------------------------------------------------- #


def test_invalid_keys_go_to_quarantine_with_reason(tmp_path):
    write_bronze(
        tmp_path / "bronze",
        "Estabelecimentos",
        [
            {"cnpj_basico": "00000001", "cnpj_ordem": "0001", "cnpj_dv": "91"},
            {"cnpj_basico": "ABC", "cnpj_ordem": "0001", "cnpj_dv": "91"},
            {"cnpj_basico": "00000002", "cnpj_ordem": "1", "cnpj_dv": "91"},
            {"cnpj_basico": "00000003", "cnpj_ordem": "0001", "cnpj_dv": "9X"},
            {"cnpj_basico": None, "cnpj_ordem": "0001", "cnpj_dv": "91"},
        ],
    )
    assert run_silver(tmp_path, ["Estabelecimentos"], ratio=1.0) == 0

    assert len(silver_rows(tmp_path, "Estabelecimentos")) == 1
    reasons = sorted(r["_reject_reason"] for r in quarantine_rows(tmp_path, "Estabelecimentos"))
    assert reasons == [
        "invalid_key:cnpj_basico",
        "invalid_key:cnpj_basico",
        "invalid_key:cnpj_dv",
        "invalid_key:cnpj_ordem",
    ]


def test_duplicates_keep_one_row_deterministically(tmp_path):
    rows = [
        {"cnpj_basico": "00000001", "razao_social": "PRIMEIRA"},
        {"cnpj_basico": "00000001", "razao_social": "SEGUNDA"},
        {"cnpj_basico": "00000002", "razao_social": "UNICA"},
    ]
    outputs = []
    for name, ordering in (("a", rows), ("b", list(reversed(rows)))):
        base = tmp_path / name
        write_bronze(base / "bronze", "Empresas", ordering)
        assert run_silver(base, ["Empresas"]) == 0
        kept = {r["cnpj_basico"]: r["razao_social"] for r in silver_rows(base, "Empresas")}
        outputs.append(kept)
        (dup,) = quarantine_rows(base, "Empresas")
        assert dup["_reject_reason"] == "duplicate_key"
        assert dup["razao_social"] != kept["00000001"]

    assert outputs[0] == outputs[1]  # a ordem de entrada nao muda quem fica
    assert len(outputs[0]) == 2


def test_socios_are_not_deduplicated(tmp_path):
    same = {"cnpj_basico": "00000001", "identificador_socio": "2", "nome_socio": "FULANO"}
    write_bronze(tmp_path / "bronze", "Socios", [same, same])
    assert run_silver(tmp_path, ["Socios"]) == 0
    assert len(silver_rows(tmp_path, "Socios")) == 2
    assert not quarantine_path(tmp_path / "silver", "Socios", MONTH).exists()


def test_domain_table(tmp_path):
    write_bronze(
        tmp_path / "bronze",
        "Cnaes",
        [
            {"codigo": "0111301", "descricao": "  Cultivo   de arroz "},
            {"codigo": None, "descricao": "SEM CODIGO"},
        ],
    )
    assert run_silver(tmp_path, ["Cnaes"], ratio=1.0) == 0
    (row,) = silver_rows(tmp_path, "Cnaes")
    assert (row["codigo"], row["descricao"]) == ("0111301", "Cultivo de arroz")


def test_quality_gate_fails_and_removes_output(tmp_path):
    write_bronze(
        tmp_path / "bronze",
        "Empresas",
        [{"cnpj_basico": "00000001"}, {"cnpj_basico": "X"}, {"cnpj_basico": "Y"}],
    )
    assert run_silver(tmp_path, ["Empresas"], ratio=0.1) == 1

    assert not silver_path(tmp_path / "silver", "Empresas", MONTH).exists()
    assert quarantine_path(tmp_path / "silver", "Empresas", MONTH).exists()  # fica para inspecao
    assert not list((tmp_path / "silver").rglob("*.tmp"))


# --------------------------------------------------------------------------- #
# Execucao: skip/force, manifesto, relatorio, CLI
# --------------------------------------------------------------------------- #


def test_skip_existing_and_force(tmp_path):
    write_bronze(tmp_path / "bronze", "Empresas", [{"cnpj_basico": "00000001", "razao_social": "ANTES"}])
    assert run_silver(tmp_path, ["Empresas"]) == 0
    out = silver_path(tmp_path / "silver", "Empresas", MONTH)
    first_mtime = out.stat().st_mtime_ns

    write_bronze(tmp_path / "bronze", "Empresas", [{"cnpj_basico": "00000001", "razao_social": "DEPOIS"}])
    assert run_silver(tmp_path, ["Empresas"]) == 0
    assert out.stat().st_mtime_ns == first_mtime
    assert silver_rows(tmp_path, "Empresas")[0]["razao_social"] == "ANTES"

    assert run_silver(tmp_path, ["Empresas"], force=True) == 0
    assert silver_rows(tmp_path, "Empresas")[0]["razao_social"] == "DEPOIS"


def test_manifest_and_report_contain_only_counts(tmp_path):
    write_bronze(
        tmp_path / "bronze",
        "Empresas",
        [
            {"cnpj_basico": "00000001", "razao_social": " SEGREDO DA EMPRESA  ", "capital_social": "x"},
            {"cnpj_basico": "BAD", "razao_social": "OUTRA"},
        ],
    )
    report_dir = tmp_path / "report"
    code = run(
        MONTH,
        ["Empresas"],
        bronze_dir=tmp_path / "bronze",
        silver_dir=tmp_path / "silver",
        report_dir=report_dir,
        force=False,
        memory_limit="1GB",
        threads=2,
        max_quarantine_ratio=0.9,
        tmp_root=tmp_path / "tmp",
    )
    assert code == 0

    manifest_path = tmp_path / "silver" / f"_manifest_{MONTH}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["tables"]["Empresas"]
    assert (entry["rows_in"], entry["rows_out"], entry["invalid_key_rows"]) == (2, 1, 1)
    assert entry["parquet"] == f"empresas/reference_month={MONTH}/empresas.parquet"
    assert entry["columns"]["capital_social"]["nullified"] == 1
    assert entry["columns"]["razao_social"]["changed"] == 1
    assert manifest["rules_status"].startswith("provisorias")

    report = (report_dir / f"{MONTH}.md").read_text(encoding="utf-8")
    assert "capital_social" in report
    assert "SEGREDO" not in report and "SEGREDO" not in manifest_path.read_text(encoding="utf-8")


def test_manifest_accumulates_tables_across_runs(tmp_path):
    write_bronze(tmp_path / "bronze", "Empresas", [{"cnpj_basico": "00000001"}])
    write_bronze(tmp_path / "bronze", "Simples", [{"cnpj_basico": "00000001"}])
    assert run_silver(tmp_path, ["Empresas"]) == 0
    assert run_silver(tmp_path, ["Simples"]) == 0
    manifest = json.loads((tmp_path / "silver" / f"_manifest_{MONTH}.json").read_text(encoding="utf-8"))
    assert set(manifest["tables"]) == {"Empresas", "Simples"}


def test_missing_bronze_returns_error(tmp_path):
    assert run_silver(tmp_path, ["Empresas"]) == 1


def test_missing_tables_are_skipped_when_others_exist(tmp_path):
    write_bronze(tmp_path / "bronze", "Empresas", [{"cnpj_basico": "00000001"}])
    assert run_silver(tmp_path, ["Empresas", "Socios"]) == 0
    assert silver_path(tmp_path / "silver", "Empresas", MONTH).exists()
    assert not silver_path(tmp_path / "silver", "Socios", MONTH).exists()


def test_render_report_without_affected_columns():
    table = {
        "rows_in": 1,
        "rows_out": 1,
        "invalid_key_rows": 0,
        "duplicate_rows_removed": 0,
        "columns": {"cnpj_basico": {"type": "VARCHAR", "rule": "key8", "nullified": 0, "changed": 0}},
    }
    assert "Nenhum valor foi anulado" in render_report(MONTH, {"Empresas": table})


@pytest.mark.parametrize(
    "argv",
    [
        ["--month", "2026-13"],
        ["--month", "outubro"],
        ["--month", MONTH, "--tables", "naoexiste"],
        ["--month", MONTH, "--max-quarantine-ratio", "2"],
    ],
)
def test_cli_rejects_bad_arguments(argv, capsys):
    assert main(argv) == 2


def test_cli_end_to_end(tmp_path):
    write_bronze(tmp_path / "bronze", "Empresas", [{"cnpj_basico": "00000001", "razao_social": "OK"}])
    argv = [
        "--month",
        MONTH,
        "--tables",
        "empresas",
        "--bronze-dir",
        str(tmp_path / "bronze"),
        "--silver-dir",
        str(tmp_path / "silver"),
        "--report-dir",
        str(tmp_path / "report"),
        "--memory-limit",
        "1GB",
        "--threads",
        "2",
    ]
    assert main(argv) == 0
    assert silver_rows(tmp_path, "Empresas")[0]["razao_social"] == "OK"
    assert (tmp_path / "report" / f"{MONTH}.md").exists()
