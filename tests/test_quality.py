from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from helpers import MONTH

from cnpj_pipeline import config
from cnpj_pipeline.gold_models import MODEL_NAMES
from cnpj_pipeline.quality import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    STATUS_WARNING,
    benchmarks,
    machine_info,
    main,
    month_end,
    peak_memory_mib,
    register_macros,
    row_rules,
    run,
)
from cnpj_pipeline.silver import quarantine_path, silver_path

# --------------------------------------------------------------------------- #
# Referencia independente do digito verificador (nao usa o SQL do modulo)
# --------------------------------------------------------------------------- #


def reference_dv(base12: str) -> str:
    def calc(chars: str, weights: list[int]) -> int:
        total = sum((ord(c) - 48) * w for c, w in zip(chars, weights, strict=True))
        rest = total % 11
        return 0 if rest < 2 else 11 - rest

    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = calc(base12, w1)
    d2 = calc(base12 + str(d1), [6, *w1])
    return f"{d1}{d2}"


def test_reference_dv_matches_published_examples():
    assert reference_dv("000000000001") == "91"  # 00.000.000/0001-91
    assert reference_dv("112223330001") == "81"  # 11.222.333/0001-81
    # Exemplo alfanumerico da documentacao oficial: 12.ABC.345/01DE-35
    assert reference_dv("12ABC34501DE") == "35"


@pytest.mark.parametrize(
    "base12", ["000000000001", "112223330001", "12ABC34501DE", "00000001000Z", "AAAAAAAA0001", "99999999ZZZZ"]
)
def test_sql_macro_matches_the_reference(base12):
    con = duckdb.connect()
    register_macros(con)
    assert con.execute("SELECT cnpj_calc_dv(?)", [base12]).fetchone()[0] == reference_dv(base12)


# --------------------------------------------------------------------------- #
# Silver sintetica e limpa (todas as regras passam)
# --------------------------------------------------------------------------- #

COLUMNS: dict[str, dict[str, str]] = {
    "Estabelecimentos": {
        "cnpj_basico": "VARCHAR", "cnpj_ordem": "VARCHAR", "cnpj_dv": "VARCHAR",
        "identificador_matriz_filial": "VARCHAR", "situacao_cadastral": "VARCHAR",
        "data_situacao_cadastral": "DATE", "data_inicio_atividade": "DATE",
        "cnae_fiscal_principal": "VARCHAR", "uf": "VARCHAR", "municipio": "VARCHAR",
    },
    "Empresas": {
        "cnpj_basico": "VARCHAR", "natureza_juridica": "VARCHAR", "porte_empresa": "VARCHAR",
        "capital_social": "DECIMAL(20,2)",
    },
    "Simples": {
        "cnpj_basico": "VARCHAR", "data_opcao_simples": "DATE", "data_exclusao_simples": "DATE",
        "data_opcao_mei": "DATE", "data_exclusao_mei": "DATE",
    },
    "Socios": {
        "cnpj_basico": "VARCHAR", "identificador_socio": "VARCHAR", "data_entrada_sociedade": "DATE",
    },
    **{t: {"codigo": "VARCHAR", "descricao": "VARCHAR"} for t in
       ("Cnaes", "Motivos", "Municipios", "Naturezas", "Paises", "Qualificacoes")},
}  # fmt: skip


def estab(basico: str, ordem: str, matriz: str, **extra) -> dict:
    row = {
        "cnpj_basico": basico, "cnpj_ordem": ordem, "cnpj_dv": reference_dv(basico + ordem),
        "identificador_matriz_filial": matriz, "situacao_cadastral": "02",
        "data_situacao_cadastral": "2021-03-10", "data_inicio_atividade": "2020-01-15",
        "cnae_fiscal_principal": "4711301", "uf": "SP", "municipio": "7107",
    }  # fmt: skip
    row.update(extra)
    return row


def clean_rows() -> dict[str, list[dict]]:
    return {
        "Estabelecimentos": [
            estab("00000001", "0001", "1"),
            estab("00000001", "0002", "2"),
            estab("12ABC345", "01DE", "2", data_situacao_cadastral="2020-01-15"),
            estab("00000002", "0001", "1", situacao_cadastral="08", uf="RJ"),
        ],
        "Empresas": [
            {"cnpj_basico": "00000001", "natureza_juridica": "2062", "porte_empresa": "05",
             "capital_social": "1000.00"},
            {"cnpj_basico": "00000002", "natureza_juridica": "2135", "porte_empresa": "01",
             "capital_social": "0.00"},
        ],
        "Simples": [
            {"cnpj_basico": "00000001", "data_opcao_simples": "2020-02-01",
             "data_exclusao_simples": "2022-02-01", "data_opcao_mei": "2020-02-01",
             "data_exclusao_mei": "2020-02-01"},
            {"cnpj_basico": "00000002", "data_opcao_simples": "2026-10-31"},
        ],
        "Socios": [
            {"cnpj_basico": "00000001", "identificador_socio": "2", "data_entrada_sociedade": "2020-01-15"},
            {"cnpj_basico": "00000001", "identificador_socio": "2", "data_entrada_sociedade": "2020-01-15"},
            {"cnpj_basico": "00000002", "identificador_socio": "1", "data_entrada_sociedade": "2026-10-31"},
        ],
        "Cnaes": [{"codigo": "4711301", "descricao": "COMERCIO"}],
        "Motivos": [{"codigo": "00", "descricao": "SEM MOTIVO"}],
        "Municipios": [{"codigo": "7107", "descricao": "SAO PAULO"}],
        "Naturezas": [{"codigo": "2062", "descricao": "LTDA"}],
        "Paises": [{"codigo": "105", "descricao": "BRASIL"}],
        "Qualificacoes": [{"codigo": "49", "descricao": "SOCIO-ADMINISTRADOR"}],
    }  # fmt: skip


def write_parquet(path: Path, columns: dict[str, str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("CREATE TABLE t (" + ", ".join(f'"{c}" {t}' for c, t in columns.items()) + ")")
    names = list(columns)
    con.executemany(
        f"INSERT INTO t VALUES ({', '.join('?' for _ in names)})",
        [[row.get(c) for c in names] for row in rows],
    )
    con.execute(f"COPY t TO '{path.as_posix()}' (FORMAT parquet)")
    con.close()


def build(tmp_path: Path, rows: dict[str, list[dict]] | None = None) -> dict[str, list[dict]]:
    rows = rows if rows is not None else clean_rows()
    for table, table_rows in rows.items():
        write_parquet(silver_path(tmp_path / "silver", table, MONTH), COLUMNS[table], table_rows)
    return rows


def run_quality(tmp_path: Path, *, quick: bool = False) -> int:
    return run(
        MONTH,
        bronze_dir=tmp_path / "bronze",
        silver_dir=tmp_path / "silver",
        gold_dir=tmp_path / "gold",
        quality_dir=tmp_path / "quality",
        report_dir=tmp_path / "report",
        quick=quick,
        memory_limit="1GB",
        threads=2,
        tmp_root=tmp_path / "tmp",
    )


def results(tmp_path: Path) -> dict[str, dict]:
    manifest = json.loads((tmp_path / "quality" / f"_manifest_{MONTH}.json").read_text(encoding="utf-8"))
    return {c["name"]: c for c in manifest["checks"]}


# --------------------------------------------------------------------------- #
# Dados limpos
# --------------------------------------------------------------------------- #


def test_clean_data_passes_every_check(tmp_path):
    build(tmp_path)
    assert run_quality(tmp_path) == 0
    checks = results(tmp_path)
    assert checks
    bad = {n: c["violations"] for n, c in checks.items() if c["status"] in (STATUS_WARNING, STATUS_FAILED)}
    assert bad == {}
    # Sem manifestos de camada, so as verificacoes que dependem deles sao puladas.
    skipped = {n for n, c in checks.items() if c["status"] == STATUS_SKIPPED}
    assert "estabelecimentos_bronze_x_silver" in skipped
    assert "gold_verificacoes_bloqueantes" in skipped
    assert checks["estabelecimento_digito_verificador"]["status"] == STATUS_OK
    assert checks["estabelecimento_digito_verificador"]["total"] == 4


def test_boundary_dates_are_not_violations(tmp_path):
    rows = clean_rows()
    rows["Estabelecimentos"][0]["data_inicio_atividade"] = "1900-01-01"
    rows["Estabelecimentos"][0]["data_situacao_cadastral"] = "1900-01-01"
    rows["Estabelecimentos"][1]["data_inicio_atividade"] = month_end(MONTH).isoformat()
    rows["Estabelecimentos"][1]["data_situacao_cadastral"] = month_end(MONTH).isoformat()
    build(tmp_path, rows)
    assert run_quality(tmp_path) == 0
    checks = results(tmp_path)
    for name in ("estabelecimento_inicio_antes_1900", "estabelecimento_inicio_futuro",
                 "estabelecimento_situacao_futura", "estabelecimento_situacao_antes_inicio"):  # fmt: skip
        assert checks[name]["violations"] == 0, name


# --------------------------------------------------------------------------- #
# Mutacao: cada regra por linha detecta exatamente o erro que descreve
# --------------------------------------------------------------------------- #

MUTATIONS = [
    ("estabelecimento_formato_cnpj", "Estabelecimentos", {"cnpj_ordem": "ab12"}),
    ("estabelecimento_digito_verificador", "Estabelecimentos", {"cnpj_dv": "00"}),
    ("estabelecimento_matriz_filial", "Estabelecimentos", {"identificador_matriz_filial": "3"}),
    ("estabelecimento_matriz_ordem", "Estabelecimentos", {"cnpj_ordem": "0003", "cnpj_dv": None}),
    ("estabelecimento_situacao", "Estabelecimentos", {"situacao_cadastral": "99"}),
    ("estabelecimento_uf_nula", "Estabelecimentos", {"uf": None}),
    ("estabelecimento_cnae_formato", "Estabelecimentos", {"cnae_fiscal_principal": "47113"}),
    ("estabelecimento_inicio_nulo", "Estabelecimentos", {"data_inicio_atividade": None}),
    ("estabelecimento_inicio_futuro", "Estabelecimentos", {"data_inicio_atividade": "2026-11-01"}),
    ("estabelecimento_inicio_antes_1900", "Estabelecimentos", {"data_inicio_atividade": "1899-12-31"}),
    ("estabelecimento_situacao_futura", "Estabelecimentos", {"data_situacao_cadastral": "2026-11-01"}),
    ("estabelecimento_situacao_antes_inicio", "Estabelecimentos", {"data_situacao_cadastral": "2019-12-31"}),
    ("empresa_porte", "Empresas", {"porte_empresa": "09"}),
    ("empresa_natureza_formato", "Empresas", {"natureza_juridica": "20A2"}),
    ("empresa_capital_negativo", "Empresas", {"capital_social": "-1.00"}),
    ("simples_exclusao_antes_opcao", "Simples", {"data_exclusao_simples": "2020-01-31"}),
    ("simples_mei_exclusao_antes_opcao", "Simples", {"data_exclusao_mei": "2020-01-31"}),
    ("simples_opcao_futura", "Simples", {"data_opcao_mei": "2026-11-01"}),
    ("socio_identificador", "Socios", {"identificador_socio": "9"}),
    ("socio_entrada_futura", "Socios", {"data_entrada_sociedade": "2026-11-01"}),
]  # fmt: skip


@pytest.mark.parametrize(("rule", "table", "change"), MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_each_row_rule_detects_its_error(tmp_path, rule, table, change):
    rows = clean_rows()
    rows[table][0].update(change)
    build(tmp_path, rows)
    run_quality(tmp_path)
    assert results(tmp_path)[rule]["violations"] == 1


def test_every_row_rule_has_a_mutation_test():
    tested = {m[0] for m in MUTATIONS}
    assert tested == {r.name for r in row_rules(MONTH)}


def test_blocking_rule_fails_the_command_and_informative_only_warns(tmp_path):
    rows = clean_rows()
    rows["Estabelecimentos"][0]["cnpj_ordem"] = "ab12"
    build(tmp_path, rows)
    assert run_quality(tmp_path) == 1
    assert results(tmp_path)["estabelecimento_formato_cnpj"]["status"] == STATUS_FAILED

    rows = clean_rows()
    rows["Estabelecimentos"][0]["cnpj_dv"] = "00"
    build(tmp_path, rows)
    assert run_quality(tmp_path) == 0
    assert results(tmp_path)["estabelecimento_digito_verificador"]["status"] == STATUS_WARNING


# --------------------------------------------------------------------------- #
# Verificacoes sobre o conjunto
# --------------------------------------------------------------------------- #


def test_duplicate_keys_fail(tmp_path):
    rows = clean_rows()
    rows["Estabelecimentos"].append(dict(rows["Estabelecimentos"][0]))
    rows["Empresas"].append(dict(rows["Empresas"][0]))
    rows["Simples"].append(dict(rows["Simples"][0]))
    rows["Cnaes"].append({"codigo": "4711301", "descricao": "REPETIDO"})
    build(tmp_path, rows)
    assert run_quality(tmp_path) == 1
    checks = results(tmp_path)
    for name in ("estabelecimento_chave_unica", "empresa_chave_unica", "simples_chave_unica",
                 "cnaes_codigo_unico"):  # fmt: skip
        assert checks[name]["violations"] == 1, name
        assert checks[name]["status"] == STATUS_FAILED, name
    assert checks["motivos_codigo_unico"]["status"] == STATUS_OK


def test_companies_without_head_office_and_partners_without_company_are_reported(tmp_path):
    rows = clean_rows()
    rows["Empresas"].append(
        {
            "cnpj_basico": "00000009",
            "natureza_juridica": "2062",
            "porte_empresa": "01",
            "capital_social": "1.00",
        }
    )
    rows["Socios"].append({"cnpj_basico": "00000077", "identificador_socio": "2"})
    rows["Socios"].append({"cnpj_basico": "00000077", "identificador_socio": "2"})
    build(tmp_path, rows)
    assert run_quality(tmp_path) == 0  # informativas: nao falham o comando
    checks = results(tmp_path)
    assert checks["empresa_sem_matriz"]["violations"] == 1
    assert checks["empresa_sem_matriz"]["status"] == STATUS_WARNING
    assert checks["socio_sem_empresa"]["violations"] == 2


def test_quick_skips_only_the_heavy_checks(tmp_path):
    rows = clean_rows()
    rows["Estabelecimentos"].append(dict(rows["Estabelecimentos"][0]))  # duplicado: so a pesada pegaria
    build(tmp_path, rows)
    assert run_quality(tmp_path, quick=True) == 0
    checks = results(tmp_path)
    for name in (
        "estabelecimento_chave_unica",
        "empresa_chave_unica",
        "empresa_sem_matriz",
        "socio_sem_empresa",
    ):
        assert checks[name]["status"] == STATUS_SKIPPED, name
    assert checks["simples_chave_unica"]["status"] == STATUS_OK
    assert checks["estabelecimento_formato_cnpj"]["status"] == STATUS_OK


# --------------------------------------------------------------------------- #
# Contagens entre camadas
# --------------------------------------------------------------------------- #


def write_layer_manifests(
    tmp_path: Path, rows: dict[str, list[dict]], *, quarantine: dict[str, int] | None = None
):
    quarantine = quarantine or {}
    silver_tables, bronze_files = {}, []
    for table, table_rows in rows.items():
        removed = quarantine.get(table, 0)
        silver_tables[table] = {
            "table": table, "status": "converted", "rows_in": len(table_rows) + removed,
            "rows_out": len(table_rows), "invalid_key_rows": removed, "duplicate_rows_removed": 0,
            "seconds": 2.0, "parquet_bytes": 1000,
        }  # fmt: skip
        bronze_files.append(
            {"table": table, "rows": len(table_rows) + removed, "parquet_bytes": 900, "seconds": 1.5}
        )
    silver = tmp_path / "silver"
    silver.mkdir(parents=True, exist_ok=True)
    (silver / f"_manifest_{MONTH}.json").write_text(json.dumps({"tables": silver_tables}), encoding="utf-8")
    bronze = tmp_path / "bronze"
    bronze.mkdir(parents=True, exist_ok=True)
    (bronze / f"_manifest_{MONTH}.json").write_text(
        json.dumps({"files": bronze_files, "total_rows": 0, "total_rejected_lines": 0}), encoding="utf-8"
    )
    for table, removed in quarantine.items():
        write_parquet(quarantine_path(silver, table, MONTH), COLUMNS[table], [{"cnpj_basico": "X"}] * removed)


def test_layer_counts_pass_when_consistent(tmp_path):
    rows = build(tmp_path)
    write_layer_manifests(tmp_path, rows, quarantine={"Empresas": 3})
    assert run_quality(tmp_path) == 0
    checks = results(tmp_path)
    for table in config.ALL_TABLES:
        for suffix in ("bronze_x_silver", "silver_conserva_linhas", "silver_parquet_x_manifesto",
                       "quarentena_x_manifesto"):  # fmt: skip
            name = f"{table.lower()}_{suffix}"
            assert checks[name]["status"] in (STATUS_OK, STATUS_SKIPPED), name
    assert checks["empresas_quarentena_x_manifesto"]["status"] == STATUS_OK
    assert checks["empresas_bronze_x_silver"]["total"] == len(rows["Empresas"]) + 3


def test_bronze_rows_that_differ_from_silver_input_fail(tmp_path):
    rows = build(tmp_path)
    write_layer_manifests(tmp_path, rows)
    path = tmp_path / "bronze" / f"_manifest_{MONTH}.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    next(f for f in manifest["files"] if f["table"] == "Socios")["rows"] += 5
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert run_quality(tmp_path) == 1
    check = results(tmp_path)["socios_bronze_x_silver"]
    assert (check["status"], check["violations"]) == (STATUS_FAILED, 5)


def test_silver_parquet_that_differs_from_its_manifest_fails(tmp_path):
    rows = build(tmp_path)
    write_layer_manifests(tmp_path, rows)
    path = tmp_path / "silver" / f"_manifest_{MONTH}.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["tables"]["Empresas"]["rows_out"] += 1  # manifesto diz uma linha a mais que o arquivo
    manifest["tables"]["Empresas"]["rows_in"] += 1
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert run_quality(tmp_path) == 1
    checks = results(tmp_path)
    assert checks["empresas_silver_parquet_x_manifesto"]["status"] == STATUS_FAILED
    assert checks["empresas_silver_conserva_linhas"]["status"] == STATUS_OK


def test_rows_lost_between_input_and_output_plus_quarantine_fail(tmp_path):
    rows = build(tmp_path)
    write_layer_manifests(tmp_path, rows)
    path = tmp_path / "silver" / f"_manifest_{MONTH}.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["tables"]["Simples"]["rows_in"] += 2  # duas linhas sumiram sem ir para a quarentena
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert run_quality(tmp_path) == 1
    check = results(tmp_path)["simples_silver_conserva_linhas"]
    assert (check["status"], check["violations"]) == (STATUS_FAILED, 2)


def test_quarantine_file_that_differs_from_the_manifest_fails(tmp_path):
    rows = build(tmp_path)
    write_layer_manifests(tmp_path, rows, quarantine={"Empresas": 2})
    quarantine_path(tmp_path / "silver", "Empresas", MONTH).unlink()
    assert run_quality(tmp_path) == 1
    check = results(tmp_path)["empresas_quarentena_x_manifesto"]
    assert (check["status"], check["violations"]) == (STATUS_FAILED, 2)


def write_gold_manifest(tmp_path: Path, *, failing: bool = False, drop_model: str | None = None) -> None:
    models = {m: {"rows": 10, "parquet_bytes": 2048, "seconds": 0.5} for m in MODEL_NAMES if m != drop_model}
    checks = [{"name": "fato_igual_silver", "ok": not failing, "blocking": True, "detail": {}},
              {"name": "informativa", "ok": False, "blocking": False, "detail": {}}]  # fmt: skip
    gold = tmp_path / "gold"
    gold.mkdir(parents=True, exist_ok=True)
    (gold / f"_manifest_{MONTH}.json").write_text(
        json.dumps({"models": models, "checks": checks}), encoding="utf-8"
    )


def test_gold_manifest_is_checked(tmp_path):
    build(tmp_path)
    write_gold_manifest(tmp_path)
    assert run_quality(tmp_path) == 0  # verificacao informativa da gold que falhou nao conta
    assert results(tmp_path)["gold_verificacoes_bloqueantes"]["status"] == STATUS_OK

    write_gold_manifest(tmp_path, failing=True)
    assert run_quality(tmp_path) == 1

    write_gold_manifest(tmp_path, drop_model="fato_estabelecimento")
    assert run_quality(tmp_path) == 1
    assert results(tmp_path)["gold_verificacoes_bloqueantes"]["violations"] == 1


# --------------------------------------------------------------------------- #
# Tempos, maquina, relatorio e linha de comando
# --------------------------------------------------------------------------- #


def test_benchmarks_sum_bronze_files_per_table():
    bronze = {"files": [
        {"table": "Empresas", "rows": 10, "parquet_bytes": 100, "seconds": 1.25},
        {"table": "Empresas", "rows": 5, "parquet_bytes": 50, "seconds": 0.5},
        {"table": "Simples", "rows": 7, "parquet_bytes": 70, "seconds": 2.0},
    ]}  # fmt: skip
    silver = {"tables": {"Empresas": {"rows_out": 14, "parquet_bytes": 90, "seconds": 3.0}}}
    gold = {"models": {"fato": {"rows": 9, "parquet_bytes": 80, "seconds": 4.0}}}
    out = benchmarks(bronze, silver, gold)
    assert out["bronze"]["Empresas"] == {"linhas": 15, "bytes": 150, "segundos": 1.75}
    assert out["silver"]["Empresas"] == {"linhas": 14, "bytes": 90, "segundos": 3.0}
    assert out["gold"]["fato"]["segundos"] == 4.0
    assert benchmarks(None, None, None) == {"bronze": {}, "silver": {}, "gold": {}}


def test_machine_info_and_peak_memory_are_reported():
    info = machine_info()
    assert {"sistema", "cpus_logicas", "memoria_total_mib", "python", "duckdb"} <= set(info)
    assert info["cpus_logicas"] and info["cpus_logicas"] >= 1
    assert info["duckdb"] == duckdb.__version__
    peak = peak_memory_mib()
    assert peak is None or peak > 0


def test_report_and_manifest_contain_only_counts(tmp_path):
    rows = clean_rows()
    secret = "EMPRESA SECRETA DE FULANO"
    rows["Cnaes"][0]["descricao"] = secret
    build(tmp_path, rows)
    write_layer_manifests(tmp_path, rows)
    write_gold_manifest(tmp_path)
    assert run_quality(tmp_path) == 0
    report = (tmp_path / "report" / f"{MONTH}.md").read_text(encoding="utf-8")
    manifest_text = (tmp_path / "quality" / f"_manifest_{MONTH}.json").read_text(encoding="utf-8")
    for text in (report, manifest_text):
        assert secret not in text
        assert "00000001" not in text  # nenhuma chave de empresa
    assert "## Verificações bloqueantes" in report
    assert "## Tempos medidos por camada" in report
    assert "## Máquina desta verificação" in report
    assert "`estabelecimento_digito_verificador`" in report
    manifest = json.loads(manifest_text)
    assert manifest["benchmarks"]["silver"]["Empresas"]["linhas"] == len(rows["Empresas"])
    assert manifest["machine"]["duckdb"] == duckdb.__version__


def test_cli_validations(tmp_path, capsys):
    assert main(["--month", "2026-13"]) == 2
    assert "invalido" in capsys.readouterr().err
    empty = ["--silver-dir", str(tmp_path / "vazio"), "--bronze-dir", str(tmp_path / "b"),
             "--gold-dir", str(tmp_path / "g"), "--quality-dir", str(tmp_path / "q"),
             "--report-dir", str(tmp_path / "r")]  # fmt: skip
    assert main(["--month", MONTH, *empty]) == 1  # nao ha silver para verificar
