from __future__ import annotations

import json
import zipfile
from pathlib import Path

import duckdb
import pytest

from cnpj_pipeline.bronze import (
    STATUS_CONVERTED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    SchemaMismatchError,
    check_layout,
    find_zip_files,
    main,
    process_zip,
    run,
)
from cnpj_pipeline.schemas import TABLE_COLUMNS

GOOD_ROWS = [
    '"12345678";"INDUSTRIA E COMERCIO DE AÇÚCAR LTDA";"2062";"49";"1000,50";"03";""',
    '"23456789";"EMPRESA COM ; PONTO E VIRGULA";"2062";"49";"0,00";"01";""',
    '"34567890";"NOME COM ""ASPAS"" INTERNAS";"2062";"49";"10,00";"01";""',
    '"67890123";"ULTIMA LINHA";"2062";"49";"1,00";"05";""',
]
BAD_ROWS = [
    '"45678901";"COLUNAS A MENOS";"2062"',
    '"56789012";"COLUNAS A MAIS";"2062";"49";"1,00";"01";"";"EXTRA"',
]


def make_zip(path: Path, members: dict[str, list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, rows in members.items():
            zf.writestr(name, ("\n".join(rows) + "\n").encode("latin-1"))
    return path


def kwargs(tmp_path: Path, **overrides):
    base = dict(
        force=False,
        memory_limit="1GB",
        threads=2,
        max_reject_ratio=0.0,
        tmp_root=tmp_path / "tmp",
    )
    base.update(overrides)
    return base


def read_parquet(path: Path) -> duckdb.DuckDBPyRelation:
    con = duckdb.connect()
    return con.execute(f"SELECT * FROM read_parquet('{path.as_posix()}', hive_partitioning=false)")


# --------------------------------------------------------------------------- #
# Localizacao
# --------------------------------------------------------------------------- #


def test_find_zip_files_orders_numerically_and_ignores_other_tables(tmp_path):
    month_dir = tmp_path / "raw" / "2026-10"
    for name in ("Empresas0.zip", "Empresas2.zip", "Empresas10.zip", "Estabelecimentos0.zip", "Cnaes.zip"):
        make_zip(month_dir / name, {"x": ["a"]})
    (month_dir / "Empresas.txt").write_text("nao e zip")

    names = [p.name for p in find_zip_files(month_dir, "Empresas")]

    assert names == ["Empresas0.zip", "Empresas2.zip", "Empresas10.zip"]
    assert [p.name for p in find_zip_files(month_dir, "Cnaes")] == ["Cnaes.zip"]
    assert find_zip_files(tmp_path / "nao-existe", "Empresas") == []


# --------------------------------------------------------------------------- #
# Validacao de layout
# --------------------------------------------------------------------------- #


def test_check_layout_accepts_matching_and_rejects_different(tmp_path):
    csv_file = tmp_path / "a.csv"
    csv_file.write_bytes(GOOD_ROWS[0].encode("utf-8") + b"\n")

    check_layout(csv_file, TABLE_COLUMNS["Empresas"])
    with pytest.raises(SchemaMismatchError, match="7 colunas|tem 7 colunas"):
        check_layout(csv_file, TABLE_COLUMNS["Socios"])


def test_check_layout_tolerates_a_few_broken_lines_but_not_a_layout_change(tmp_path):
    good = GOOD_ROWS[0].encode("utf-8")
    broken = BAD_ROWS[0].encode("utf-8")

    # Linha quebrada logo no inicio: a maioria das linhas bate com o esquema
    tolerant = tmp_path / "tolerant.csv"
    tolerant.write_bytes(broken + b"\n" + (good + b"\n") * 20)
    check_layout(tolerant, TABLE_COLUMNS["Empresas"])

    # Layout diferente em todas as linhas: deve falhar
    changed = tmp_path / "changed.csv"
    changed.write_bytes((broken + b"\n") * 20 + good + b"\n")
    with pytest.raises(SchemaMismatchError, match="tem 3 colunas"):
        check_layout(changed, TABLE_COLUMNS["Empresas"])


# --------------------------------------------------------------------------- #
# Conversao
# --------------------------------------------------------------------------- #


def test_control_bytes_c1_do_not_break_conversion_and_are_counted(tmp_path):
    # 0x8F e um controle C1: o decodificador latin-1 do DuckDB o recusa, a bronze deve aceitar.
    # 0x1A (SUB) aparece em dados reais no lugar de letras acentuadas perdidas.
    rows = [
        '"98765432";"RAZAO COM \x8f\x8f CONTROLE";"2062";"49";"1,00";"01";""',
        '"87654321";"ASUNCI\x1aN";"2062";"49";"1,00";"01";""',
        GOOD_ROWS[0],
    ]
    zip_path = make_zip(tmp_path / "raw" / "Empresas0.zip", {"K3241.EMPRECSV": rows})
    bronze = tmp_path / "bronze"

    results = process_zip(zip_path, "Empresas", "2026-10", bronze, **kwargs(tmp_path))

    assert results[0].status == STATUS_CONVERTED
    assert results[0].rows == 3
    assert results[0].rejected_lines == 0
    assert results[0].control_chars == 2
    assert results[0].csv_bytes == zip_path_member_size(zip_path)
    out = bronze / "empresas" / "reference_month=2026-10" / "Empresas0.parquet"
    values = {r[0]: r[1] for r in read_parquet(out).fetchall()}
    assert values["98765432"] == "RAZAO COM \x8f\x8f CONTROLE"  # bronze preserva o dado de origem
    assert values["87654321"] == "ASUNCI\x1aN"


def zip_path_member_size(zip_path: Path) -> int:
    with zipfile.ZipFile(zip_path) as zf:
        return zf.infolist()[0].file_size


def test_converts_latin1_csv_to_parquet(tmp_path):
    zip_path = make_zip(tmp_path / "raw" / "Empresas0.zip", {"K3241.EMPRECSV": GOOD_ROWS})
    bronze = tmp_path / "bronze"

    results = process_zip(zip_path, "Empresas", "2026-10", bronze, **kwargs(tmp_path))

    assert len(results) == 1
    result = results[0]
    assert result.status == STATUS_CONVERTED
    assert result.rows == 4
    assert result.rejected_lines == 0

    out = bronze / "empresas" / "reference_month=2026-10" / "Empresas0.parquet"
    assert out.exists()
    rel = read_parquet(out)
    columns = [d[0] for d in rel.description]
    assert columns == [*TABLE_COLUMNS["Empresas"], "_source_file", "_reference_month", "_ingested_at"]

    rows = {r[0]: r for r in read_parquet(out).fetchall()}
    assert rows["12345678"][1] == "INDUSTRIA E COMERCIO DE AÇÚCAR LTDA"  # latin-1 -> UTF-8
    assert rows["23456789"][1] == "EMPRESA COM ; PONTO E VIRGULA"  # separador dentro de aspas
    assert rows["34567890"][1] == 'NOME COM "ASPAS" INTERNAS'  # aspas duplicadas
    assert rows["12345678"][4] == "1000,50"  # sem conversao de tipo na bronze
    assert rows["12345678"][6] is None  # campo vazio vira NULL
    assert rows["12345678"][7] == "Empresas0.zip"
    assert rows["12345678"][8] == "2026-10"


def test_all_business_columns_are_varchar(tmp_path):
    zip_path = make_zip(tmp_path / "raw" / "Empresas0.zip", {"m": GOOD_ROWS})
    process_zip(zip_path, "Empresas", "2026-10", tmp_path / "bronze", **kwargs(tmp_path))

    out = tmp_path / "bronze" / "empresas" / "reference_month=2026-10" / "Empresas0.parquet"
    con = duckdb.connect()
    types = {
        r[0]: r[1]
        for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{out.as_posix()}', hive_partitioning=false)"
        ).fetchall()
    }
    for column in TABLE_COLUMNS["Empresas"]:
        assert types[column] == "VARCHAR"


def test_malformed_lines_go_to_rejects_not_lost_silently(tmp_path):
    zip_path = make_zip(tmp_path / "raw" / "Empresas0.zip", {"m": [*GOOD_ROWS[:2], *BAD_ROWS, GOOD_ROWS[3]]})
    bronze = tmp_path / "bronze"

    results = process_zip(zip_path, "Empresas", "2026-10", bronze, **kwargs(tmp_path, max_reject_ratio=1.0))

    assert results[0].status == STATUS_CONVERTED
    assert results[0].rows == 3
    assert results[0].rejected_lines == 2

    rejects = bronze / "_rejects" / "empresas" / "reference_month=2026-10" / "Empresas0.parquet"
    assert rejects.exists()
    lines = {
        r[0]
        for r in duckdb.connect()
        .execute(f"SELECT DISTINCT line FROM read_parquet('{rejects.as_posix()}')")
        .fetchall()
    }
    assert lines == {3, 4}


def test_broken_first_line_does_not_break_the_file(tmp_path):
    zip_path = make_zip(tmp_path / "raw" / "Empresas0.zip", {"m": [BAD_ROWS[0], *GOOD_ROWS]})
    bronze = tmp_path / "bronze"

    results = process_zip(zip_path, "Empresas", "2026-10", bronze, **kwargs(tmp_path, max_reject_ratio=1.0))

    assert results[0].status == STATUS_CONVERTED
    assert results[0].rows == 4
    assert results[0].rejected_lines == 1


def test_crlf_line_endings_are_handled(tmp_path):
    zip_path = tmp_path / "raw" / "Empresas0.zip"
    zip_path.parent.mkdir(parents=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("m", ("\r\n".join(GOOD_ROWS) + "\r\n").encode("latin-1"))
    bronze = tmp_path / "bronze"

    results = process_zip(zip_path, "Empresas", "2026-10", bronze, **kwargs(tmp_path))

    assert results[0].status == STATUS_CONVERTED
    out = bronze / "empresas" / "reference_month=2026-10" / "Empresas0.parquet"
    last_column = [row[6] for row in read_parquet(out).fetchall()]
    assert last_column == [None, None, None, None]  # sem \r sobrando


def test_fails_and_removes_output_when_reject_ratio_exceeded(tmp_path):
    zip_path = make_zip(tmp_path / "raw" / "Empresas0.zip", {"m": [GOOD_ROWS[0], *BAD_ROWS]})
    bronze = tmp_path / "bronze"

    results = process_zip(zip_path, "Empresas", "2026-10", bronze, **kwargs(tmp_path, max_reject_ratio=0.01))

    assert results[0].status == STATUS_FAILED
    assert "rejeitadas" in results[0].error
    assert not (bronze / "empresas" / "reference_month=2026-10" / "Empresas0.parquet").exists()
    assert (bronze / "_rejects" / "empresas" / "reference_month=2026-10" / "Empresas0.parquet").exists()


def test_schema_mismatch_fails_without_writing(tmp_path):
    eleven_columns = [";".join(f'"{i}"' for i in range(11))]
    zip_path = make_zip(tmp_path / "raw" / "Empresas0.zip", {"m": eleven_columns})

    results = process_zip(zip_path, "Empresas", "2026-10", tmp_path / "bronze", **kwargs(tmp_path))

    assert results[0].status == STATUS_FAILED
    assert "11 colunas" in results[0].error
    assert not list((tmp_path / "bronze").rglob("*.parquet"))


def test_skip_existing_and_force(tmp_path):
    zip_path = make_zip(tmp_path / "raw" / "Empresas0.zip", {"m": GOOD_ROWS})
    bronze = tmp_path / "bronze"
    process_zip(zip_path, "Empresas", "2026-10", bronze, **kwargs(tmp_path))

    second = process_zip(zip_path, "Empresas", "2026-10", bronze, **kwargs(tmp_path))
    forced = process_zip(zip_path, "Empresas", "2026-10", bronze, **kwargs(tmp_path, force=True))

    assert second[0].status == STATUS_SKIPPED
    assert second[0].rows == 4
    assert forced[0].status == STATUS_CONVERTED


def test_domain_table_with_extensionless_member(tmp_path):
    rows = ['"0111301";"Cultivo de arroz"', '"0111302";"Cultivo de milho"']
    zip_path = make_zip(tmp_path / "raw" / "Cnaes.zip", {"F.K03200$Z.D41011.CNAECSV": rows})

    results = process_zip(zip_path, "Cnaes", "2026-10", tmp_path / "bronze", **kwargs(tmp_path))

    assert results[0].status == STATUS_CONVERTED
    assert results[0].rows == 2
    assert results[0].member == "F.K03200$Z.D41011.CNAECSV"


def test_multiple_members_create_one_parquet_each(tmp_path):
    zip_path = make_zip(tmp_path / "raw" / "Empresas0.zip", {"a": GOOD_ROWS[:2], "b": GOOD_ROWS[2:]})

    results = process_zip(zip_path, "Empresas", "2026-10", tmp_path / "bronze", **kwargs(tmp_path))

    assert [r.parquet.split("/")[-1] for r in results] == ["Empresas0_0.parquet", "Empresas0_1.parquet"]
    assert sum(r.rows for r in results) == 4


def test_invalid_zip_is_reported_as_failure(tmp_path):
    bad = tmp_path / "raw" / "Empresas0.zip"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"isto nao e um zip")

    results = process_zip(bad, "Empresas", "2026-10", tmp_path / "bronze", **kwargs(tmp_path))

    assert results[0].status == STATUS_FAILED
    assert "zip invalido" in results[0].error


def test_temporary_files_are_cleaned_up(tmp_path):
    zip_path = make_zip(tmp_path / "raw" / "Empresas0.zip", {"m": GOOD_ROWS})
    process_zip(zip_path, "Empresas", "2026-10", tmp_path / "bronze", **kwargs(tmp_path))

    assert list((tmp_path / "tmp").iterdir()) == []
    assert not list((tmp_path / "bronze").rglob("*.tmp"))


# --------------------------------------------------------------------------- #
# run e CLI
# --------------------------------------------------------------------------- #


def run_kwargs(tmp_path: Path, **overrides):
    base = dict(
        raw_dir=tmp_path / "raw",
        bronze_dir=tmp_path / "bronze",
        force=False,
        memory_limit="1GB",
        threads=2,
        max_reject_ratio=0.0,
        tmp_root=tmp_path / "tmp",
    )
    base.update(overrides)
    return base


def test_run_writes_manifest_with_totals(tmp_path):
    make_zip(tmp_path / "raw" / "2026-10" / "Empresas0.zip", {"m": GOOD_ROWS})
    make_zip(tmp_path / "raw" / "2026-10" / "Empresas1.zip", {"m": GOOD_ROWS[:2]})
    make_zip(tmp_path / "raw" / "2026-10" / "Cnaes.zip", {"m": ['"1";"a"']})

    code = run("2026-10", ["Empresas", "Cnaes", "Socios"], **run_kwargs(tmp_path))  # Socios sem zip: aviso

    assert code == 0
    manifest = json.loads((tmp_path / "bronze" / "_manifest_2026-10.json").read_text(encoding="utf-8"))
    assert manifest["total_rows"] == 4 + 2 + 1
    assert manifest["total_rejected_lines"] == 0
    assert [f["parquet"] for f in manifest["files"]] == [
        "cnaes/reference_month=2026-10/Cnaes.parquet",
        "empresas/reference_month=2026-10/Empresas0.parquet",
        "empresas/reference_month=2026-10/Empresas1.parquet",
    ]


def test_run_returns_error_when_nothing_to_process(tmp_path):
    assert run("2026-10", ["Empresas"], **run_kwargs(tmp_path)) == 1


def test_run_returns_error_when_any_file_fails(tmp_path):
    make_zip(tmp_path / "raw" / "2026-10" / "Empresas0.zip", {"m": GOOD_ROWS})
    make_zip(tmp_path / "raw" / "2026-10" / "Empresas1.zip", {"m": [";".join('"x"' for _ in range(3))]})

    assert run("2026-10", ["Empresas"], **run_kwargs(tmp_path)) == 1


def test_cli_rejects_invalid_month(capsys):
    assert main(["--month", "outubro"]) == 2
    assert "invalido" in capsys.readouterr().err
