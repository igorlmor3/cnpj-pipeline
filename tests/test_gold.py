from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from helpers import MONTH, write_bronze

from cnpj_pipeline import config
from cnpj_pipeline.cloud.publish import collect_files
from cnpj_pipeline.gold import gold_path, main, run
from cnpj_pipeline.gold_models import (
    CNAE_ORG_POLITICA,
    CNAE_SECOES,
    MODEL_NAMES,
    MODELS,
    MODELS_BY_NAME,
    secao_case,
    values_sql,
)
from cnpj_pipeline.silver import run as run_silver_layer

SECRET_NAME = "EMPRESA SECRETA DE FULANO"

ESTABELECIMENTOS = [
    # matriz e filial ativas em SP, mesma empresa (00000001)
    {
        "cnpj_basico": "00000001", "cnpj_ordem": "0001", "cnpj_dv": "91",
        "identificador_matriz_filial": "1", "situacao_cadastral": "02",
        "data_inicio_atividade": "20200115", "cnae_fiscal_principal": "4711301",
        "uf": "SP", "municipio": "7107", "correio_eletronico": "contato@secreta.com.br",
        "telefone_1": "99999999", "logradouro": "RUA SECRETA",
    },
    {
        "cnpj_basico": "00000001", "cnpj_ordem": "0002", "cnpj_dv": "72",
        "identificador_matriz_filial": "2", "situacao_cadastral": "02",
        "data_inicio_atividade": "20200301", "cnae_fiscal_principal": "4711301",
        "uf": "SP", "municipio": "7107",
    },
    # matriz baixada no RJ
    {
        "cnpj_basico": "00000002", "cnpj_ordem": "0001", "cnpj_dv": "55",
        "identificador_matriz_filial": "1", "situacao_cadastral": "08",
        "data_inicio_atividade": "20180510", "cnae_fiscal_principal": "6201501",
        "uf": "RJ", "municipio": "6001",
    },
    # matriz ativa em MG, municipio sem linha na dimensao
    {
        "cnpj_basico": "00000003", "cnpj_ordem": "0001", "cnpj_dv": "10",
        "identificador_matriz_filial": "1", "situacao_cadastral": "02",
        "data_inicio_atividade": "20200920", "cnae_fiscal_principal": "0111301",
        "uf": "MG", "municipio": "4123",
    },
    # matriz ativa sem empresa, sem data valida, UF invalida e CNAE fora da dimensao
    {
        "cnpj_basico": "00000004", "cnpj_ordem": "0001", "cnpj_dv": "20",
        "identificador_matriz_filial": "1", "situacao_cadastral": "02",
        "data_inicio_atividade": "00000000", "cnae_fiscal_principal": "9999999",
        "uf": "XX", "municipio": "7107",
    },
]  # fmt: skip

EMPRESAS = [
    {"cnpj_basico": "00000001", "razao_social": SECRET_NAME, "natureza_juridica": "2062",
     "porte_empresa": "05", "capital_social": "1000,00"},
    {"cnpj_basico": "00000002", "razao_social": "OUTRA", "natureza_juridica": "2062",
     "porte_empresa": "01", "capital_social": "50,00"},
    {"cnpj_basico": "00000003", "razao_social": "TERCEIRA", "natureza_juridica": "2135",
     "porte_empresa": "03", "capital_social": "20000,00"},
]  # fmt: skip

SIMPLES = [
    {"cnpj_basico": "00000001", "opcao_pelo_simples": "S", "opcao_pelo_mei": "N"},
    {"cnpj_basico": "00000003", "opcao_pelo_simples": "S", "opcao_pelo_mei": "S"},
]

SOCIOS = [
    {"cnpj_basico": "00000001", "identificador_socio": "2", "nome_socio": "FULANO DE TAL",
     "qualificacao_socio": "49"},
    {"cnpj_basico": "00000001", "identificador_socio": "2", "nome_socio": "CICRANO",
     "qualificacao_socio": "49"},
    {"cnpj_basico": "00000002", "identificador_socio": "1", "qualificacao_socio": "05"},
    {"cnpj_basico": "00000003", "identificador_socio": "2", "qualificacao_socio": "99"},
]  # fmt: skip

DOMAINS = {
    "Cnaes": [("4711301", "COMERCIO VAREJISTA"), ("6201501", "DESENVOLVIMENTO DE SOFTWARE"),
              ("0111301", "CULTIVO DE ARROZ")],
    "Municipios": [("7107", "SAO PAULO"), ("6001", "RIO DE JANEIRO")],
    "Naturezas": [("2062", "SOCIEDADE EMPRESARIA LIMITADA"), ("2135", "EMPRESARIO INDIVIDUAL")],
    "Qualificacoes": [("49", "SOCIO-ADMINISTRADOR"), ("05", "ADMINISTRADOR")],
}  # fmt: skip


# Organizacoes politicas (CNAE 9492800) e uma associacao da mesma divisao 94 com outro CNAE.
POLITICAS_ESTABELECIMENTOS = [
    {"cnpj_basico": "00000005", "cnpj_ordem": "0001", "cnpj_dv": "11",
     "identificador_matriz_filial": "1", "situacao_cadastral": "02",
     "data_inicio_atividade": "20200915", "cnae_fiscal_principal": "9492800",
     "uf": "SP", "municipio": "7107"},
    {"cnpj_basico": "00000006", "cnpj_ordem": "0001", "cnpj_dv": "22",
     "identificador_matriz_filial": "1", "situacao_cadastral": "08",
     "data_inicio_atividade": "20200920", "cnae_fiscal_principal": "9492800",
     "uf": "SP", "municipio": "7107"},
    {"cnpj_basico": "00000007", "cnpj_ordem": "0001", "cnpj_dv": "33",
     "identificador_matriz_filial": "1", "situacao_cadastral": "02",
     "data_inicio_atividade": "20200920", "cnae_fiscal_principal": "9491000",
     "uf": "SP", "municipio": "7107"},
    # CNAE principal vazio na origem: o flag deve ser FALSE, nunca nulo
    {"cnpj_basico": "00000008", "cnpj_ordem": "0001", "cnpj_dv": "44",
     "identificador_matriz_filial": "1", "situacao_cadastral": "08",
     "data_inicio_atividade": "20180510", "cnae_fiscal_principal": None,
     "uf": "RJ", "municipio": "6001"},
]  # fmt: skip
POLITICAS_EMPRESAS = [
    {"cnpj_basico": "00000005", "natureza_juridica": "4090", "porte_empresa": "05", "capital_social": "0,00"},
    {"cnpj_basico": "00000006", "natureza_juridica": "4090", "porte_empresa": "05", "capital_social": "0,00"},
    {"cnpj_basico": "00000007", "natureza_juridica": "3999", "porte_empresa": "03",
     "capital_social": "10,00"},
]  # fmt: skip


def build_silver(tmp_path: Path, *, with_political: bool = False) -> None:
    bronze = tmp_path / "bronze"
    extra_estab = POLITICAS_ESTABELECIMENTOS if with_political else []
    extra_emp = POLITICAS_EMPRESAS if with_political else []
    write_bronze(bronze, "Estabelecimentos", ESTABELECIMENTOS + extra_estab)
    write_bronze(bronze, "Empresas", EMPRESAS + extra_emp)
    write_bronze(bronze, "Simples", SIMPLES)
    write_bronze(bronze, "Socios", SOCIOS)
    for table, rows in DOMAINS.items():
        write_bronze(bronze, table, [{"codigo": c, "descricao": d} for c, d in rows])
    code = run_silver_layer(
        MONTH,
        list(config.ALL_TABLES),
        bronze_dir=bronze,
        silver_dir=tmp_path / "silver",
        report_dir=None,
        force=False,
        memory_limit="1GB",
        threads=2,
        max_quarantine_ratio=0.5,
        tmp_root=tmp_path / "tmp",
    )
    assert code == 0


def run_gold(tmp_path: Path, models: list[str] | None = None, *, force: bool = False) -> int:
    return run(
        MONTH,
        models,
        silver_dir=tmp_path / "silver",
        gold_dir=tmp_path / "gold",
        report_dir=None,
        force=force,
        memory_limit="1GB",
        threads=2,
        tmp_root=tmp_path / "tmp",
    )


def read(tmp_path: Path, model: str, order_by: str) -> list[dict]:
    path = gold_path(tmp_path / "gold", model, MONTH)
    con = duckdb.connect()
    try:
        cur = con.execute(
            f"SELECT * FROM read_parquet('{path.as_posix()}', hive_partitioning=false) ORDER BY {order_by}"
        )
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def manifest(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "gold" / f"_manifest_{MONTH}.json").read_text(encoding="utf-8"))


@pytest.fixture
def gold_dir(tmp_path) -> Path:
    build_silver(tmp_path)
    assert run_gold(tmp_path) == 0
    return tmp_path


# --------------------------------------------------------------------------- #
# Definicoes estaticas
# --------------------------------------------------------------------------- #


def test_cnae_sections_do_not_overlap_and_are_ordered():
    letters = [s[0] for s in CNAE_SECOES]
    assert letters == sorted(set(letters))
    previous_end = 0
    for _, description, start, end in CNAE_SECOES:
        assert description
        assert previous_end < start <= end
        previous_end = end


def test_secao_case_maps_divisions():
    con = duckdb.connect()
    cases = {1: "A", 3: "A", 4: None, 5: "B", 10: "C", 33: "C", 47: "G", 62: "J", 85: "P", 99: "U"}
    for division, expected in cases.items():
        assert con.execute(f"SELECT {secao_case(str(division))}").fetchone()[0] == expected, division
    assert con.execute(f"SELECT {secao_case('NULL')}").fetchone()[0] is None


def test_values_sql_escapes_quotes():
    con = duckdb.connect()
    sql = values_sql((("1", "D'Agua"),), ("codigo", "descricao"))
    assert con.execute(f"SELECT t.descricao FROM {sql}").fetchone()[0] == "D'Agua"


def test_models_are_in_dependency_order_and_named_uniquely():
    assert len(set(MODEL_NAMES)) == len(MODEL_NAMES)
    seen: set[str] = set()
    for model in MODELS:
        assert set(model.depends) <= seen, model.name
        assert set(model.sources) <= set(config.ALL_TABLES), model.name
        seen.add(model.name)
    assert set(MODELS_BY_NAME) == set(MODEL_NAMES)


# --------------------------------------------------------------------------- #
# Conteudo dos modelos
# --------------------------------------------------------------------------- #


def test_all_models_are_built_in_the_expected_layout(gold_dir):
    for name in MODEL_NAMES:
        path = gold_path(gold_dir / "gold", name, MONTH)
        assert path.exists(), name
        assert path.parent.name == f"reference_month={MONTH}"
    assert not list((gold_dir / "gold").rglob("*.tmp"))


def test_fato_has_one_row_per_cnpj_with_derived_columns(gold_dir):
    rows = {r["cnpj"]: r for r in read(gold_dir, "fato_estabelecimento", "cnpj")}

    assert len(rows) == 5
    matriz = rows["00000001000191"]
    assert matriz["is_matriz"] is True and matriz["is_ativo"] is True
    assert matriz["ano_inicio_atividade"] == 2020
    assert matriz["ano_mes_inicio_atividade"] == "2020-01"
    assert matriz["cnae_divisao"] == "47" and matriz["cnae_secao"] == "G"
    assert matriz["natureza_juridica"] == "2062" and matriz["porte_empresa"] == "05"
    assert matriz["capital_social"] == Decimal("1000.00")
    assert matriz["is_optante_simples"] is True and matriz["is_mei"] is False
    assert matriz["is_organizacao_politica"] is False
    assert matriz["_reference_month"] == MONTH

    assert rows["00000001000272"]["is_matriz"] is False
    assert rows["00000002000155"]["is_ativo"] is False  # baixada
    assert rows["00000003000110"]["is_mei"] is True

    orphan = rows["00000004000120"]
    assert orphan["uf"] is None  # UF invalida virou nula na silver
    assert orphan["data_inicio_atividade"] is None and orphan["ano_mes_inicio_atividade"] is None
    assert orphan["natureza_juridica"] is None and orphan["porte_empresa"] is None  # sem empresa
    assert orphan["is_optante_simples"] is False and orphan["is_mei"] is False  # sem Simples
    assert orphan["cnae_secao"] == "U"


def test_fato_has_no_contact_name_or_address_columns(gold_dir):
    columns = set(manifest(gold_dir)["models"]["fato_estabelecimento"]["columns"])

    forbidden = {
        "razao_social", "nome_fantasia", "correio_eletronico", "telefone_1", "ddd_1", "fax",
        "cep", "logradouro", "numero", "complemento", "bairro", "nome_socio", "capital_social_texto",
    }  # fmt: skip
    assert not columns & forbidden


def test_gold_files_do_not_contain_names_or_contacts(gold_dir):
    con = duckdb.connect()
    for path in (gold_dir / "gold").rglob("*.parquet"):
        values = con.execute(
            f"SELECT * FROM read_parquet('{path.as_posix()}', hive_partitioning=false)"
        ).fetchall()
        text = json.dumps(values, default=str, ensure_ascii=False)
        for secret in (SECRET_NAME, "FULANO", "secreta.com.br", "RUA SECRETA", "99999999"):
            assert secret not in text, (path.name, secret)


def test_dimensions(gold_dir):
    cnae = {r["codigo"]: r for r in read(gold_dir, "dim_cnae", "codigo")}
    assert cnae["4711301"]["secao"] == "G" and cnae["4711301"]["divisao"] == "47"
    assert cnae["0111301"]["secao"] == "A"

    natureza = {r["codigo"]: r for r in read(gold_dir, "dim_natureza_juridica", "codigo")}
    assert natureza["2062"]["grupo"] == "2"
    assert natureza["2062"]["grupo_descricao"] == "Entidades Empresariais"

    assert [r["codigo"] for r in read(gold_dir, "dim_situacao_cadastral", "codigo")] == [
        "01", "02", "03", "04", "08",
    ]  # fmt: skip
    assert {r["codigo"]: r["descricao"] for r in read(gold_dir, "dim_porte", "codigo")}["01"] == (
        "Microempresa"
    )
    sections = read(gold_dir, "dim_cnae_secao", "codigo")
    assert len(sections) == len(CNAE_SECOES) and sections[0]["codigo"] == "A"
    assert [r["descricao"] for r in read(gold_dir, "dim_municipio", "codigo")] == [
        "RIO DE JANEIRO", "SAO PAULO",
    ]  # fmt: skip


def test_mart_aberturas_mensal(gold_dir):
    rows = read(gold_dir, "mart_aberturas_mensal", "ano_mes, uf")
    got = {(r["ano_mes"], r["uf"], r["cnae_secao"]): r for r in rows}

    assert set(got) == {
        ("2018-05", "RJ", "J"),
        ("2020-01", "SP", "G"),
        ("2020-03", "SP", "G"),
        ("2020-09", "MG", "A"),
    }
    assert sum(r["estabelecimentos_abertos"] for r in rows) == 4  # a linha sem data fica de fora
    rj = got[("2018-05", "RJ", "J")]
    assert rj["ano"] == 2018 and rj["matrizes_abertas"] == 1 and rj["ainda_ativos"] == 0
    sp = got[("2020-03", "SP", "G")]
    assert sp["matrizes_abertas"] == 0 and sp["ainda_ativos"] == 1


def test_mart_estabelecimentos_ativos(gold_dir):
    rows = read(gold_dir, "mart_estabelecimentos_ativos", "uf NULLS LAST, municipio")
    got = {(r["uf"], r["municipio"], r["cnae_divisao"]): r for r in rows}

    assert sum(r["estabelecimentos_ativos"] for r in rows) == 4  # a baixada fica de fora
    sp = got[("SP", "7107", "47")]
    assert sp["municipio_nome"] == "SAO PAULO"
    assert sp["estabelecimentos_ativos"] == 2 and sp["matrizes_ativas"] == 1
    assert sp["optantes_simples"] == 2 and sp["optantes_mei"] == 0
    assert sp["cnae_secao_descricao"] == "Comércio; reparação de veículos automotores e motocicletas"
    mg = got[("MG", "4123", "01")]
    assert mg["municipio_nome"] is None  # municipio fora da dimensao
    assert mg["optantes_mei"] == 1
    assert got[(None, "7107", "99")]["cnae_secao"] == "U"


def test_mart_perfil_porte_natureza_counts_active_companies_once(gold_dir):
    rows = read(gold_dir, "mart_perfil_porte_natureza", "porte_empresa NULLS LAST")
    got = {(r["porte_empresa"], r["natureza_juridica"]): r for r in rows}

    assert sum(r["empresas_ativas"] for r in rows) == 3  # SP, MG e a sem empresa; a baixada fica de fora
    micro_ltda = got[("05", "2062")]
    assert micro_ltda["empresas_ativas"] == 1  # matriz e filial contam uma empresa
    assert micro_ltda["porte_descricao"] == "Demais"
    assert micro_ltda["natureza_grupo"] == "Entidades Empresariais"
    assert micro_ltda["capital_social_total"] == Decimal("1000.00")
    assert micro_ltda["capital_social_mediano"] == Decimal("1000.00")
    assert got[(None, None)]["empresas_ativas"] == 1


def test_mart_simples_mei_uf(gold_dir):
    got = {r["uf"]: r for r in read(gold_dir, "mart_simples_mei_uf", "uf NULLS LAST")}

    assert got["SP"]["empresas_ativas"] == 1 and got["SP"]["pct_simples"] == 100.0
    assert got["SP"]["optantes_mei"] == 0 and got["SP"]["pct_mei"] == 0.0
    assert got["MG"]["optantes_simples"] == 1 and got["MG"]["optantes_mei"] == 1
    assert got[None]["optantes_simples"] == 0 and got[None]["pct_simples"] == 0.0
    assert "RJ" not in got  # a unica empresa do RJ esta baixada


def test_mart_socios_qualificacao(gold_dir):
    rows = read(gold_dir, "mart_socios_qualificacao", "qualificacao_socio")
    got = {(r["qualificacao_socio"], r["identificador_socio"]): r for r in rows}

    assert sum(r["socios"] for r in rows) == 4
    assert got[("49", "2")]["socios"] == 2
    assert got[("49", "2")]["qualificacao_descricao"] == "SOCIO-ADMINISTRADOR"
    assert got[("49", "2")]["tipo_socio"] == "Pessoa física"
    assert got[("05", "1")]["tipo_socio"] == "Pessoa jurídica"
    assert got[("99", "2")]["qualificacao_descricao"] is None


# --------------------------------------------------------------------------- #
# Organizacoes politicas (CNAE 9492800)
# --------------------------------------------------------------------------- #


@pytest.fixture
def political_dir(tmp_path) -> Path:
    build_silver(tmp_path, with_political=True)
    assert run_gold(tmp_path) == 0
    return tmp_path


def test_cnae_org_politica_constant_is_the_political_organizations_code():
    assert CNAE_ORG_POLITICA == "9492800"


def test_fato_marks_only_the_political_cnae(political_dir):
    rows = {r["cnpj"]: r for r in read(political_dir, "fato_estabelecimento", "cnpj")}

    assert len(rows) == 9
    flagged = {cnpj for cnpj, r in rows.items() if r["is_organizacao_politica"]}
    assert flagged == {"00000005000111", "00000006000122"}
    # mesma divisao 94, outro CNAE: nao e organizacao politica
    assert rows["00000007000133"]["cnae_divisao"] == "94"
    assert rows["00000007000133"]["is_organizacao_politica"] is False
    # o flag nunca e nulo, nem quando o CNAE principal vem vazio
    assert rows["00000008000144"]["cnae_principal"] is None
    assert rows["00000008000144"]["is_organizacao_politica"] is False
    assert all(r["is_organizacao_politica"] in (True, False) for r in rows.values())


def test_aberturas_separates_the_political_group(political_dir):
    rows = read(political_dir, "mart_aberturas_mensal", "ano_mes, uf, is_organizacao_politica")
    got = {(r["ano_mes"], r["uf"], r["cnae_secao"], r["is_organizacao_politica"]): r for r in rows}

    politica = got[("2020-09", "SP", "S", True)]
    assert politica["estabelecimentos_abertos"] == 2
    assert politica["matrizes_abertas"] == 2 and politica["ainda_ativos"] == 1
    outras = got[("2020-09", "SP", "S", False)]
    assert outras["estabelecimentos_abertos"] == 1  # a associacao de CNAE 9491000
    # as demais linhas continuam sem a marcacao
    assert all(not r["is_organizacao_politica"] for k, r in got.items() if k[:3] != ("2020-09", "SP", "S"))


def test_other_marts_separate_the_political_group(political_dir):
    ativos = {
        (r["uf"], r["municipio"], r["cnae_divisao"], r["is_organizacao_politica"]): r
        for r in read(political_dir, "mart_estabelecimentos_ativos", "uf NULLS LAST, municipio")
    }
    assert ativos[("SP", "7107", "94", True)]["estabelecimentos_ativos"] == 1  # a baixada fica de fora
    assert ativos[("SP", "7107", "94", False)]["estabelecimentos_ativos"] == 1

    perfil = {
        (r["porte_empresa"], r["natureza_juridica"], r["is_organizacao_politica"]): r
        for r in read(political_dir, "mart_perfil_porte_natureza", "porte_empresa NULLS LAST")
    }
    assert perfil[("05", "4090", True)]["empresas_ativas"] == 1
    assert perfil[("03", "3999", False)]["empresas_ativas"] == 1
    assert perfil[("05", "2062", False)]["empresas_ativas"] == 1  # as demais empresas nao mudam

    simples = {
        (r["uf"], r["is_organizacao_politica"]): r
        for r in read(political_dir, "mart_simples_mei_uf", "uf NULLS LAST")
    }
    politica = simples[("SP", True)]
    assert politica["empresas_ativas"] == 1 and politica["optantes_simples"] == 0
    assert politica["pct_simples"] == 0.0
    # sem a organizacao politica, o percentual e calculado so sobre as demais empresas de SP
    outras = simples[("SP", False)]
    assert outras["empresas_ativas"] == 2 and outras["optantes_simples"] == 1
    assert outras["pct_simples"] == 50.0


def test_political_checks_exist_and_pass(political_dir):
    checks = {c["name"]: c for c in manifest(political_dir)["checks"]}

    assert all(c["ok"] for c in checks.values() if c["blocking"])
    for name in ("fato_org_politica_igual_silver", "mart_aberturas_org_politica_soma"):
        assert checks[name]["blocking"] is True and checks[name]["ok"] is True, name
        assert checks[name]["detail"] == {"esquerda": 2, "direita": 2}, name
    # os totais gerais continuam fechando com o fato, agora somando os dois grupos
    assert (
        checks["mart_aberturas_soma"]["detail"]["esquerda"]
        == checks["mart_aberturas_soma"]["detail"]["direita"]
    )
    assert checks["mart_ativos_soma"]["ok"] and checks["mart_simples_empresas_soma"]["ok"]


def test_standard_data_has_no_political_rows(gold_dir):
    checks = {c["name"]: c for c in manifest(gold_dir)["checks"]}
    assert checks["fato_org_politica_igual_silver"]["detail"] == {"esquerda": 0, "direita": 0}
    assert checks["mart_aberturas_org_politica_soma"]["detail"] == {"esquerda": 0, "direita": 0}


# --------------------------------------------------------------------------- #
# Verificacoes, manifesto e relatorio
# --------------------------------------------------------------------------- #


def test_checks_pass_and_orphans_are_reported(gold_dir):
    checks = {c["name"]: c for c in manifest(gold_dir)["checks"]}

    assert all(c["ok"] for c in checks.values())
    blocking = {n for n, c in checks.items() if c["blocking"]}
    assert {"fato_igual_silver", "fato_chave_unica", "mart_aberturas_soma", "mart_socios_soma"} <= blocking
    assert "dim_cnae_codigo_unico" in blocking

    orphans = checks["fato_chaves_sem_correspondencia"]
    assert orphans["blocking"] is False
    assert orphans["detail"] == {
        "estabelecimentos": 5,
        "uf_nula": 1,
        "data_inicio_nula": 1,
        "natureza_nula_ou_sem_empresa": 1,
        "cnae_sem_dimensao": 1,
        "municipio_sem_dimensao": 1,
        "natureza_sem_dimensao": 0,
    }


def test_blocking_check_failure_returns_error(gold_dir):
    fato = gold_path(gold_dir / "gold", "fato_estabelecimento", MONTH)
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT * FROM read_parquet('{fato.as_posix()}') "
        f"UNION ALL SELECT * FROM read_parquet('{fato.as_posix()}') LIMIT 7) "
        f"TO '{fato.with_name('dup.parquet').as_posix()}' (FORMAT parquet)"
    )
    con.close()
    fato.unlink()
    fato.with_name("dup.parquet").rename(fato)

    assert run_gold(gold_dir) == 1  # sem --force: so as verificacoes rodam de novo
    checks = {c["name"]: c for c in manifest(gold_dir)["checks"]}
    assert not checks["fato_igual_silver"]["ok"]
    assert not checks["fato_chave_unica"]["ok"]


def test_manifest_and_report_contain_only_counts(tmp_path):
    build_silver(tmp_path)
    report_dir = tmp_path / "report"
    code = run(
        MONTH,
        None,
        silver_dir=tmp_path / "silver",
        gold_dir=tmp_path / "gold",
        report_dir=report_dir,
        force=False,
        memory_limit="1GB",
        threads=2,
        tmp_root=tmp_path / "tmp",
    )
    assert code == 0

    report = (report_dir / f"{MONTH}.md").read_text(encoding="utf-8")
    manifest_text = (tmp_path / "gold" / f"_manifest_{MONTH}.json").read_text(encoding="utf-8")
    for text in (report, manifest_text):
        for secret in (SECRET_NAME, "FULANO", "RUA SECRETA", "secreta.com.br"):
            assert secret not in text
    assert "fato_estabelecimento" in report and "Verificações bloqueantes" in report
    assert "natureza_nula_ou_sem_empresa" in report


# --------------------------------------------------------------------------- #
# Idempotencia, dependencias e entradas ausentes
# --------------------------------------------------------------------------- #


def test_rerun_skips_existing_and_force_rebuilds(gold_dir):
    fato = gold_path(gold_dir / "gold", "fato_estabelecimento", MONTH)
    before = fato.stat().st_mtime_ns

    assert run_gold(gold_dir) == 0
    assert fato.stat().st_mtime_ns == before

    assert run_gold(gold_dir, force=True) == 0
    assert fato.stat().st_mtime_ns != before


def test_rebuilding_a_dependency_rebuilds_dependents(gold_dir):
    mart = gold_path(gold_dir / "gold", "mart_simples_mei_uf", MONTH)
    before = mart.stat().st_mtime_ns

    assert run_gold(gold_dir, ["fato_estabelecimento", "mart_simples_mei_uf"], force=False) == 0
    assert mart.stat().st_mtime_ns == before  # nada refeito

    gold_path(gold_dir / "gold", "fato_estabelecimento", MONTH).unlink()
    assert run_gold(gold_dir, ["fato_estabelecimento", "mart_simples_mei_uf"]) == 0
    assert mart.stat().st_mtime_ns != before  # o fato foi refeito, entao o mart tambem


def test_forcing_only_the_fato_also_rebuilds_the_marts(gold_dir):
    root = gold_dir / "gold"
    fato = gold_path(root, "fato_estabelecimento", MONTH)
    marts = [gold_path(root, m, MONTH) for m in ("mart_aberturas_mensal", "mart_simples_mei_uf")]
    dim = gold_path(root, "dim_cnae", MONTH)
    before = {p: p.stat().st_mtime_ns for p in (fato, dim, *marts)}

    assert run_gold(gold_dir, ["fato_estabelecimento"], force=True) == 0

    assert fato.stat().st_mtime_ns != before[fato]
    for mart in marts:
        assert mart.stat().st_mtime_ns != before[mart]  # dependentes refeitos junto
    assert dim.stat().st_mtime_ns == before[dim]  # quem o fato usa nao e refeito


def test_models_without_inputs_are_skipped(tmp_path):
    bronze = tmp_path / "bronze"
    write_bronze(bronze, "Municipios", [{"codigo": "7107", "descricao": "SAO PAULO"}])
    assert (
        run_silver_layer(
            MONTH,
            ["Municipios"],
            bronze_dir=bronze,
            silver_dir=tmp_path / "silver",
            report_dir=None,
            force=False,
            memory_limit="1GB",
            threads=2,
            max_quarantine_ratio=0.5,
            tmp_root=tmp_path / "tmp",
        )
        == 0
    )

    assert run_gold(tmp_path) == 0

    assert gold_path(tmp_path / "gold", "dim_municipio", MONTH).exists()
    assert gold_path(tmp_path / "gold", "dim_porte", MONTH).exists()  # tabela fixa, sem silver
    assert not gold_path(tmp_path / "gold", "fato_estabelecimento", MONTH).exists()
    assert not gold_path(tmp_path / "gold", "mart_aberturas_mensal", MONTH).exists()
    assert set(manifest(tmp_path)["models"]) >= {"dim_municipio", "dim_porte"}


def test_missing_silver_returns_error(tmp_path):
    assert run_gold(tmp_path) == 1
    assert not (tmp_path / "gold").exists()


def test_failed_model_is_reported_and_skips_dependents(gold_dir):
    silver = (
        gold_dir / "silver" / "estabelecimentos" / f"reference_month={MONTH}" / "estabelecimentos.parquet"
    )
    silver.write_bytes(b"isso nao e parquet")
    gold_path(gold_dir / "gold", "fato_estabelecimento", MONTH).unlink()

    assert run_gold(gold_dir, ["fato_estabelecimento", "mart_aberturas_mensal"]) == 1
    assert not list((gold_dir / "gold").rglob("*.tmp"))


# --------------------------------------------------------------------------- #
# Publicacao e linha de comando
# --------------------------------------------------------------------------- #


def test_gold_layout_is_what_the_publisher_expects(gold_dir):
    files = collect_files(gold_dir, "gold", MONTH)
    keys = {key for _, key in files}

    assert f"gold/fato_estabelecimento/reference_month={MONTH}/fato_estabelecimento.parquet" in keys
    assert f"gold/_manifest_{MONTH}.json" in keys
    assert len([k for k in keys if k.endswith(".parquet")]) == len(MODEL_NAMES)


@pytest.mark.parametrize(
    "argv",
    [
        ["--month", "2026-13"],
        ["--month", "outubro"],
        ["--month", "2026-10", "--models", "dim_inexistente"],
    ],
)
def test_cli_rejects_bad_arguments(argv, capsys):
    assert main(argv) == 2
    assert capsys.readouterr().err


def test_cli_end_to_end(tmp_path):
    build_silver(tmp_path)
    argv = [
        "--month", MONTH,
        "--silver-dir", str(tmp_path / "silver"),
        "--gold-dir", str(tmp_path / "gold"),
        "--report-dir", str(tmp_path / "report"),
        "--memory-limit", "1GB",
        "--threads", "2",
    ]  # fmt: skip
    assert main(argv) == 0
    assert (tmp_path / "report" / f"{MONTH}.md").exists()
    assert main([*argv, "--models", "dim_porte"]) == 0
