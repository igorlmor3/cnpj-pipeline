from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

import duckdb
import pytest

from cnpj_pipeline import config
from cnpj_pipeline.bronze import check_layout
from cnpj_pipeline.demo import main as demo_main
from cnpj_pipeline.demo import run_demo
from cnpj_pipeline.sample import DEFAULT_MONTH, generate
from cnpj_pipeline.sample import main as sample_main
from cnpj_pipeline.schemas import columns_for
from cnpj_pipeline.silver import quarantine_path, silver_path


def read_member(zip_path: Path) -> bytes:
    with zipfile.ZipFile(zip_path) as zf:
        (name,) = zf.namelist()
        return zf.read(name)


# --------------------------------------------------------------------------- #
# Gerador de amostra
# --------------------------------------------------------------------------- #


def test_generate_creates_one_zip_per_expected_file(tmp_path):
    created = generate(DEFAULT_MONTH, tmp_path, rows=200, parts=2)

    assert set(created) == set(config.ALL_TABLES)
    assert [p.name for p in created["Empresas"]] == ["Empresas0.zip", "Empresas1.zip"]
    assert [p.name for p in created["Socios"]] == ["Socios0.zip", "Socios1.zip"]
    assert [p.name for p in created["Cnaes"]] == ["Cnaes.zip"]
    assert [p.name for p in created["Simples"]] == ["Simples.zip"]
    assert all(p.exists() for paths in created.values() for p in paths)


def test_generated_csv_matches_schema_and_is_latin1(tmp_path, tmp_path_factory):
    created = generate(DEFAULT_MONTH, tmp_path, rows=200)
    work = tmp_path_factory.mktemp("csv")

    for table, paths in created.items():
        # Parte 0 comeca pela linha quebrada plantada, entao confira o layout em outra linha
        raw = read_member(paths[-1])
        text = raw.decode("latin-1")  # nao pode falhar
        csv_file = work / f"{table}.csv"
        csv_file.write_bytes(raw)
        check_layout(csv_file, columns_for(table))
        assert text.endswith("\n")


def test_generated_files_have_planted_defects(tmp_path):
    created = generate(DEFAULT_MONTH, tmp_path, rows=500)
    lines = read_member(created["Empresas"][0]).decode("latin-1").splitlines()

    rows = list(csv.reader(lines, delimiter=";", quotechar='"'))
    widths = [len(r) for r in rows]
    assert widths.count(7) == len(rows) - 1  # uma linha quebrada, as demais com 7 colunas
    assert any(r[1] != r[1].strip() for r in rows if len(r) == 7)  # espacos nas bordas
    assert any("," not in r[4] for r in rows if len(r) == 7)  # decimal com ponto


def test_generate_is_deterministic(tmp_path):
    a = generate(DEFAULT_MONTH, tmp_path / "a", rows=100, seed=7)
    b = generate(DEFAULT_MONTH, tmp_path / "b", rows=100, seed=7)
    c = generate(DEFAULT_MONTH, tmp_path / "c", rows=100, seed=8)

    assert read_member(a["Empresas"][0]) == read_member(b["Empresas"][0])
    assert read_member(a["Empresas"][0]) != read_member(c["Empresas"][0])


def test_sample_cli_validates_arguments(tmp_path, capsys):
    assert sample_main(["--month", "x"]) == 2
    assert sample_main(["--rows", "5"]) == 2
    assert sample_main(["--out-dir", str(tmp_path), "--rows", "50"]) == 0
    assert "gerado(s)" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Demo ponta a ponta
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("demo")
    assert run_demo(base, rows=400, threads=2) == 0
    return base


def test_demo_produces_bronze_and_reports(demo_dir):
    month = DEFAULT_MONTH
    manifest = json.loads((demo_dir / "bronze" / f"_manifest_{month}.json").read_text(encoding="utf-8"))

    assert manifest["total_rows"] > 0
    assert manifest["total_rejected_lines"] == 3  # uma linha quebrada em cada tabela grande
    tables_in_manifest = {f["table"] for f in manifest["files"]}
    assert tables_in_manifest == set(config.ALL_TABLES)
    for name in ("empresas.md", "estabelecimentos.json", "socios.md", "referential.md"):
        assert (demo_dir / "profiling" / month / name).exists(), name
    assert list((demo_dir / "bronze" / "_rejects").rglob("*.parquet"))
    assert not list((demo_dir / "tmp").glob("bronze_*"))  # temporarios apagados


def test_demo_profiling_finds_the_planted_defects(demo_dir):
    report = demo_dir / "profiling" / DEFAULT_MONTH
    empresas = json.loads((report / "empresas.json").read_text(encoding="utf-8"))
    estab = json.loads((report / "estabelecimentos.json").read_text(encoding="utf-8"))
    referential = json.loads((report / "referential.json").read_text(encoding="utf-8"))

    assert empresas["key"]["duplicated_keys"] > 0
    assert empresas["columns"]["capital_social"]["decimal_comma"]["not_matching"] > 0
    assert empresas["columns"]["razao_social"]["edge_whitespace"] > 0
    date = estab["columns"]["data_inicio_atividade"]["date"]
    assert date["sentinel_zero"] > 0 and date["invalid"] > 0

    orphans = {(f["child"], f["child_column"]): f["orphan_rows"] for f in referential}
    assert orphans[("Estabelecimentos", "cnpj_basico")] > 0
    assert orphans[("Empresas", "natureza_juridica")] > 0
    assert orphans[("Socios", "cnpj_basico")] == 0


def test_demo_silver_types_and_quarantine(demo_dir):
    silver = demo_dir / "silver"
    manifest = json.loads((silver / f"_manifest_{DEFAULT_MONTH}.json").read_text(encoding="utf-8"))
    assert set(manifest["tables"]) == set(config.ALL_TABLES)

    empresas = manifest["tables"]["Empresas"]
    assert empresas["invalid_key_rows"] == 3  # chaves invalidas plantadas
    assert empresas["duplicate_rows_removed"] > 0
    assert empresas["rows_out"] == empresas["rows_in"] - 3 - empresas["duplicate_rows_removed"]
    assert manifest["tables"]["Estabelecimentos"]["invalid_key_rows"] == 2

    con = duckdb.connect()
    path = silver_path(silver, "Estabelecimentos", DEFAULT_MONTH).as_posix()
    described = {
        r[0]: r[1]
        for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{path}', hive_partitioning=false)"
        ).fetchall()
    }
    assert described["data_inicio_atividade"] == "DATE"
    assert described["cnae_fiscal_secundaria"] == "VARCHAR[]"
    assert described["cnpj"] == "VARCHAR"

    # Toda linha da silver tem CNPJ de 14 posicoes
    bad = con.execute(
        f"SELECT count(*) FROM read_parquet('{path}', hive_partitioning=false) WHERE length(cnpj) <> 14"
    ).fetchone()[0]
    assert bad == 0

    quarantine = quarantine_path(silver, "Empresas", DEFAULT_MONTH).as_posix()
    reasons = {
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT _reject_reason FROM read_parquet('{quarantine}', hive_partitioning=false)"
        ).fetchall()
    }
    assert reasons == {"invalid_key:cnpj_basico", "duplicate_key"}
    assert (demo_dir / "silver_report" / f"{DEFAULT_MONTH}.md").exists()


def test_demo_refuses_to_run_on_the_real_data_folder():
    assert run_demo(config.DATA_DIR) == 2
    assert run_demo(config.RAW_DIR) == 2


def test_demo_cli_validates_rows(capsys):
    assert demo_main(["--rows", "3"]) == 2
