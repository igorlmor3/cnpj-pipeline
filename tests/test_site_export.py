"""Testes da exportacao de agregados da gold para JSON (dados do site).

O foco e o que torna a saida segura para publicar: so agregados, nenhuma chave fora da lista permitida,
nada de dado individual, recusa quando a gold nao reconcilia, supressao de recortes pequenos e saida
identica a cada execucao.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import pytest
import test_gold
from helpers import MONTH
from test_gold import SECRET_NAME, build_silver, run_gold

from cnpj_pipeline import site_export
from cnpj_pipeline.gold import gold_path
from cnpj_pipeline.site_export import (
    ALLOWED_KEYS,
    BUILDERS,
    ExportError,
    build_documents,
    collect_keys,
    main,
    pct,
    render,
    run,
    suppress,
)

EXPECTED_FILES = {f"{name}.json" for name in (*BUILDERS, "pipeline")} | {"index.json"}


@pytest.fixture
def gold(tmp_path) -> Path:
    build_silver(tmp_path)
    assert run_gold(tmp_path) == 0
    return tmp_path / "gold"


@pytest.fixture
def political(tmp_path) -> Path:
    build_silver(tmp_path, with_political=True)
    assert run_gold(tmp_path) == 0
    return tmp_path / "gold"


def export(gold_dir: Path, out: Path, *, min_cell: int = 1) -> Path:
    return run(MONTH, gold_dir=gold_dir, out_dir=out, min_cell=min_cell)


def load(directory: Path, name: str) -> dict:
    return json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))


def fato(gold_dir: Path, sql: str):
    path = gold_path(gold_dir, "fato_estabelecimento", MONTH)
    con = duckdb.connect()
    try:
        view = f"read_parquet('{path.as_posix()}', hive_partitioning=false)"
        return con.execute(sql.replace("FATO", view)).fetchall()
    finally:
        con.close()


def manifest_path(gold_dir: Path) -> Path:
    return gold_dir / f"_manifest_{MONTH}.json"


# --------------------------------------------------------------------------- #
# Funcoes puras
# --------------------------------------------------------------------------- #


def test_pct_handles_zero_and_rounds():
    assert pct(1, 3) == 33.33
    assert pct(1, 3, 1) == 33.3
    assert pct(5, 0) is None
    assert pct(0, 10) == 0.0


def test_suppress_keeps_the_threshold_and_counts_the_rest():
    items = [{"n": 4}, {"n": 5}, {"n": 6}, {"n": None}]
    kept, omitted = suppress(items, "n", 5)
    assert [r["n"] for r in kept] == [5, 6]
    assert omitted == 2


def test_collect_keys_does_not_descend_into_dynamic_objects():
    doc = {"dados": [{"nome": "x", "detalhe": {"esquerda": 1}}], "ressalvas": {"foto_unica": "t"}}
    assert collect_keys(doc) == {"dados", "nome", "detalhe", "ressalvas"}


def test_every_file_has_a_title_description_and_known_caveats():
    assert set(site_export.FILES) == {*BUILDERS, "pipeline"}
    for title, description, caveats in site_export.FILES.values():
        assert title
        assert description
        assert set(caveats) <= set(site_export.RESSALVAS)


def test_uf_names_cover_the_27_states_the_federal_district_and_abroad():
    assert len(site_export.UF_NOMES) == 28
    assert site_export.UF_NOMES["EX"] == "Exterior"


# --------------------------------------------------------------------------- #
# Saida
# --------------------------------------------------------------------------- #


def test_export_writes_the_expected_files(gold, tmp_path):
    out = tmp_path / "site"
    target = export(gold, out)
    assert target == out / MONTH
    assert {p.name for p in target.iterdir()} == EXPECTED_FILES
    months = json.loads((out / "months.json").read_text(encoding="utf-8"))
    assert months == {"esquema": 1, "meses": [MONTH], "ultimo": MONTH}


def test_every_file_is_compact_utf8_json_with_a_trailing_newline(gold, tmp_path):
    target = export(gold, tmp_path / "site")
    for path in target.iterdir():
        raw = path.read_bytes()
        assert raw.endswith(b"\n")
        assert b"\r" not in raw
        assert b"\n" not in raw[:-1]
        json.loads(raw.decode("utf-8"))


def test_accents_are_written_as_utf8_not_escaped(gold, tmp_path):
    target = export(gold, tmp_path / "site")
    raw = (target / "uf.json").read_bytes()
    assert "São Paulo".encode() in raw
    assert b"\\u00" not in raw


def test_every_document_has_the_envelope(gold, tmp_path):
    target = export(gold, tmp_path / "site")
    for name in (*BUILDERS, "pipeline"):
        doc = load(target, name)
        assert doc["arquivo"] == name
        assert doc["esquema"] == site_export.SCHEMA_VERSION
        assert doc["mes_referencia"] == MONTH
        assert doc["titulo"]
        assert doc["descricao"]
        assert set(doc["ressalvas"]) == set(site_export.FILES[name][2])
        assert "dados" in doc


def test_index_lists_every_file_with_size_and_hash(gold, tmp_path):
    import hashlib

    target = export(gold, tmp_path / "site")
    index = load(target, "index")
    assert index["mes_referencia"] == MONTH
    listed = {a["arquivo"] for a in index["arquivos"]}
    assert listed == EXPECTED_FILES - {"index.json"}
    for entry in index["arquivos"]:
        raw = (target / entry["arquivo"]).read_bytes()
        assert entry["bytes"] == len(raw)
        assert entry["sha256"] == hashlib.sha256(raw).hexdigest()


def test_two_runs_give_identical_bytes(gold, tmp_path):
    first = export(gold, tmp_path / "a")
    second = export(gold, tmp_path / "b")
    again = export(gold, tmp_path / "a")
    for name in EXPECTED_FILES:
        assert (first / name).read_bytes() == (second / name).read_bytes() == (again / name).read_bytes()


def test_output_has_no_timestamps(gold, tmp_path):
    target = export(gold, tmp_path / "site")
    for path in target.iterdir():
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}", text), path.name
        assert "generated_at" not in text


# --------------------------------------------------------------------------- #
# Privacidade
# --------------------------------------------------------------------------- #


def test_no_key_outside_the_allowlist_and_no_dead_allowances(political, tmp_path):
    bronze_manifest = {
        "total_rows": 10,
        "total_rejected_lines": 0,
        "total_csv_bytes": 100,
        "total_parquet_bytes": 50,
        "files": [
            {"table": "Cnaes", "rows": 4, "csv_bytes": 40, "parquet_bytes": 20},
            {"table": "Cnaes", "rows": 6, "csv_bytes": 60, "parquet_bytes": 30},
        ],
        "reference_month": MONTH,
        "generated_at": "2026-11-14T00:00:00+00:00",
    }
    (political.parent / "bronze").mkdir(exist_ok=True)
    (political.parent / "bronze" / f"_manifest_{MONTH}.json").write_text(json.dumps(bronze_manifest))
    quality = {
        "reference_month": MONTH,
        "generated_at": "2026-11-14T00:00:00+00:00",
        "quick": False,
        "memory_limit": "8GB",
        "total_seconds": 12.5,
        "peak_memory_mib": 900,
        "machine": {"cpu": "secreto"},
        "checks": [{"blocking": True, "status": "ok"}, {"blocking": False, "status": "aviso"}],
        "seconds_by_step": {"gold": 3.0},
        "benchmarks": {},
    }
    (political.parent / "quality").mkdir(exist_ok=True)
    (political.parent / "quality" / f"_manifest_{MONTH}.json").write_text(json.dumps(quality))
    target = export(political, tmp_path / "site")
    used: set[str] = set()
    for path in target.iterdir():
        used |= collect_keys(json.loads(path.read_text(encoding="utf-8")))
    used |= collect_keys(json.loads((target.parent / "months.json").read_text(encoding="utf-8")))
    assert used <= ALLOWED_KEYS
    assert ALLOWED_KEYS - used == set()
    text = " ".join(p.read_text(encoding="utf-8") for p in target.iterdir())
    assert "secreto" not in text
    assert "2026-11-14T" not in text


def test_no_individual_data_leaks_into_any_file(gold, tmp_path):
    target = export(gold, tmp_path / "site")
    forbidden = [
        SECRET_NAME,
        "FULANO",
        "CICRANO",
        "contato@secreta",
        "RUA SECRETA",
        "99999999",
        "00000001",
        "00000002",
        "00000003",
    ]
    for path in target.iterdir():
        text = path.read_text(encoding="utf-8")
        for secret in forbidden:
            assert secret not in text, (secret, path.name)
        for key in collect_keys(json.loads(text)):
            assert not re.search(r"cnpj|razao|nome_socio|email|correio|telefone|logradouro", key), key


def test_no_cross_of_municipality_and_activity(gold, tmp_path):
    target = export(gold, tmp_path / "site")
    for path in target.iterdir():
        keys = collect_keys(json.loads(path.read_text(encoding="utf-8")))
        assert not ({"municipio_nome"} & keys and {"secao", "cnae_divisao", "divisao"} & keys), path.name


def test_the_export_never_reads_the_fato(gold, tmp_path):
    for name in site_export.SOURCES:
        assert "fato" not in name
    fato_file = gold_path(gold, "fato_estabelecimento", MONTH)
    hidden = fato_file.with_suffix(".hidden")
    fato_file.rename(hidden)
    try:
        export(gold, tmp_path / "site")
    finally:
        hidden.rename(fato_file)


# --------------------------------------------------------------------------- #
# Reconciliacao com a gold
# --------------------------------------------------------------------------- #


def test_overview_matches_independent_counts_from_the_fato(gold, tmp_path):
    target = export(gold, tmp_path / "site")
    overview = load(target, "overview")["dados"]
    (ativos,) = fato(gold, "SELECT count(*) FROM FATO WHERE is_ativo")[0]
    (empresas,) = fato(gold, "SELECT count(*) FROM FATO WHERE is_ativo AND is_matriz")[0]
    (simples,) = fato(gold, "SELECT count(*) FROM FATO WHERE is_ativo AND is_matriz AND is_optante_simples")[
        0
    ]
    (mei,) = fato(gold, "SELECT count(*) FROM FATO WHERE is_ativo AND is_matriz AND is_mei")[0]
    (cadastro,) = fato(gold, "SELECT count(*) FROM FATO WHERE data_inicio_atividade IS NOT NULL")[0]
    assert overview["estabelecimentos_ativos"] == ativos > 0
    assert overview["todas"]["empresas_ativas"] == empresas > 0
    assert overview["todas"]["optantes_simples"] == simples
    assert overview["todas"]["optantes_mei"] == mei
    assert overview["estabelecimentos_no_cadastro"] == cadastro
    assert overview["todas"]["pct_simples"] == pct(simples, empresas)
    assert overview["total_socios"] == 4


def test_sum_by_uf_equals_the_national_totals(gold, tmp_path):
    target = export(gold, tmp_path / "site")
    overview = load(target, "overview")["dados"]
    uf = load(target, "uf")["dados"]
    assert sum(r["estabelecimentos_ativos"] for r in uf) == overview["estabelecimentos_ativos"]
    assert sum(r["empresas_ativas"] for r in uf) == overview["todas"]["empresas_ativas"]
    assert sum(r["optantes_simples"] for r in uf) == overview["todas"]["optantes_simples"]
    assert overview["ufs"] == len([r for r in uf if r["uf"] is not None])


def test_uf_rows_carry_names_and_percentages_from_counts(gold, tmp_path):
    uf = {r["uf"]: r for r in load(export(gold, tmp_path / "site"), "uf")["dados"]}
    assert uf["SP"]["uf_nome"] == "São Paulo"
    for r in uf.values():
        assert r["pct_simples"] == pct(r["optantes_simples"], r["empresas_ativas"])
        assert r["pct_mei"] == pct(r["optantes_mei"], r["empresas_ativas"])
    ordered = load(export(gold, tmp_path / "site2"), "uf")["dados"]
    counts = [r["estabelecimentos_ativos"] for r in ordered]
    assert counts == sorted(counts, reverse=True)


def test_openings_by_year_and_month_add_up_to_the_cadastre(gold, tmp_path):
    target = export(gold, tmp_path / "site")
    overview = load(target, "overview")["dados"]
    by_year = load(target, "aberturas_ano")["dados"]
    by_month = load(target, "aberturas_mes")["dados"]
    total = overview["estabelecimentos_no_cadastro"]
    assert sum(r["estabelecimentos_abertos"] for r in by_year) == total
    assert sum(r["estabelecimentos_abertos"] for r in by_month) == total
    for r in by_year:
        assert r["estabelecimentos_abertos"] == r["abertos_org_politica"] + r["abertos_demais"]
    assert [r["ano"] for r in by_year] == sorted(r["ano"] for r in by_year)
    assert [r["ano_mes"] for r in by_month] == sorted(r["ano_mes"] for r in by_month)


def test_monthly_openings_separate_the_political_group(political, tmp_path):
    by_month = {r["ano_mes"]: r for r in load(export(political, tmp_path / "site"), "aberturas_mes")["dados"]}
    assert sum(r["abertos_org_politica"] for r in by_month.values()) == 2
    assert by_month["2020-09"]["abertos_org_politica"] == 2
    assert by_month["2020-09"]["abertos_demais"] == 2  # MG 00000003 e a associacao 00000007
    for r in by_month.values():
        assert r["estabelecimentos_abertos"] == r["abertos_org_politica"] + r["abertos_demais"]


def test_overview_without_political_group_subtracts_its_simples_and_mei(tmp_path, monkeypatch):
    simples = [
        *test_gold.SIMPLES,
        {"cnpj_basico": "00000005", "opcao_pelo_simples": "S", "opcao_pelo_mei": "S"},
    ]
    monkeypatch.setattr(test_gold, "SIMPLES", simples)
    build_silver(tmp_path, with_political=True)
    assert run_gold(tmp_path) == 0
    overview = load(export(tmp_path / "gold", tmp_path / "site"), "overview")["dados"]
    todas, sem = overview["todas"], overview["sem_org_politica"]
    assert todas["optantes_simples"] - sem["optantes_simples"] == 1
    assert todas["optantes_mei"] - sem["optantes_mei"] == 1
    assert sem["pct_simples"] == pct(sem["optantes_simples"], sem["empresas_ativas"])


def test_share_of_the_largest_states_uses_the_configured_count(gold, tmp_path, monkeypatch):
    monkeypatch.setattr(site_export, "TOP_UFS", 1)
    overview = load(export(gold, tmp_path / "a"), "overview")["dados"]
    assert overview["participacao_cinco_maiores_ufs_pct"] == overview["participacao_maior_uf_pct"]
    monkeypatch.setattr(site_export, "TOP_UFS", 2)
    two = load(export(gold, tmp_path / "b"), "overview")["dados"]
    assert two["participacao_cinco_maiores_ufs_pct"] > two["participacao_maior_uf_pct"]


def test_the_political_group_is_separated_everywhere(political, tmp_path):
    target = export(political, tmp_path / "site")
    overview = load(target, "overview")["dados"]
    (pol_total,) = fato(political, "SELECT count(*) FROM FATO WHERE is_organizacao_politica")[0]
    assert overview["aberturas_org_politica"] == pol_total == 2
    by_year = {r["ano"]: r for r in load(target, "aberturas_ano")["dados"]}
    assert by_year[2020]["abertos_org_politica"] == 2
    assert sum(r["abertos_org_politica"] for r in by_year.values()) == pol_total
    # as aberturas por secao e por UF nao incluem o grupo politico
    by_section = load(target, "aberturas_secao_ano")["dados"]
    by_uf = load(target, "aberturas_uf_ano")["dados"]
    total_other = sum(r["abertos_demais"] for r in by_year.values() if r["ano"] >= 2015)
    assert sum(r["aberturas"] for r in by_section) == total_other
    assert sum(r["aberturas"] for r in by_uf) == total_other
    # o ativo politico (00000005) entra so no grupo de organizacoes politicas
    assert overview["empresas_ativas_org_politica"] == 1
    todas, sem = overview["todas"], overview["sem_org_politica"]
    assert todas["empresas_ativas"] - sem["empresas_ativas"] == 1
    assert todas["optantes_simples"] - sem["optantes_simples"] == 0


def test_section_and_active_files_use_the_dimension_descriptions(gold, tmp_path):
    target = export(gold, tmp_path / "site")
    sections = load(target, "aberturas_secao_ano")["secoes"]
    assert {s["codigo"] for s in sections} == set("ABCDEFGHIJKLMNOPQRSTU")
    ativos = load(target, "ativos_secao")["dados"]
    assert (
        sum(r["estabelecimentos_ativos"] for r in ativos)
        == load(target, "overview")["dados"]["estabelecimentos_ativos"]
    )
    assert all(r["secao_descricao"] for r in ativos if r["secao"])


def test_profile_files_add_up_and_share_sums_to_one_hundred(gold, tmp_path):
    target = export(gold, tmp_path / "site")
    total = load(target, "overview")["dados"]["todas"]["empresas_ativas"]
    porte = load(target, "perfil_porte")["dados"]
    assert sum(r["empresas_ativas"] for r in porte) == total
    assert round(sum(r["participacao_pct"] for r in porte), 1) == pytest.approx(100.0, abs=0.1)
    natureza = load(target, "perfil_natureza")
    grupos = natureza["grupos"]
    assert sum(g["empresas_ativas"] for g in grupos) == total
    in_top = sum(r["empresas_ativas"] for r in natureza["dados"])
    assert in_top + natureza["outras"]["empresas_ativas"] == total


def test_natureza_keeps_the_top_n_and_groups_the_rest(gold, tmp_path, monkeypatch):
    monkeypatch.setattr(site_export, "TOP_NATUREZAS", 1)
    natureza = load(export(gold, tmp_path / "site"), "perfil_natureza")
    assert len(natureza["dados"]) == 1
    assert natureza["outras"]["empresas_ativas"] > 0
    assert natureza["dados"][0]["empresas_ativas"] >= natureza["outras"]["empresas_ativas"] - 1


def test_median_capital_excludes_the_political_group_and_nulls(political, tmp_path):
    rows = load(export(political, tmp_path / "site"), "capital_mediano")["dados"]
    assert rows
    for r in rows:
        assert r["capital_social_mediano"] is not None
        assert r["empresas"] >= 1
    # a natureza 4090 so existe no grupo politico: nao pode aparecer
    assert "4090" not in {r["natureza"] for r in rows}


def test_median_capital_keeps_only_the_most_common_naturezas(gold, tmp_path, monkeypatch):
    monkeypatch.setattr(site_export, "CAPITAL_NATUREZAS", 1)
    rows = load(export(gold, tmp_path / "site"), "capital_mediano")["dados"]
    assert len({r["natureza"] for r in rows}) == 1


def test_socios_file_has_totals_by_type_and_top_qualifications(gold, tmp_path):
    doc = load(export(gold, tmp_path / "site"), "socios")
    assert sum(t["socios"] for t in doc["tipos"]) == 4
    assert sum(r["socios"] for r in doc["dados"]) == 4
    assert {t["codigo"] for t in doc["tipos"]} <= {"1", "2", "3"}


def test_socios_top_n_limits_qualifications(gold, tmp_path, monkeypatch):
    monkeypatch.setattr(site_export, "TOP_QUALIFICACOES", 1)
    rows = load(export(gold, tmp_path / "site"), "socios")["dados"]
    assert len({r["qualificacao"] for r in rows}) == 1
    assert sum(r["socios"] for r in rows) == 2  # a qualificacao 49 tem dois vinculos


def test_top_municipalities_are_ranked_and_limited(gold, tmp_path, monkeypatch):
    rows = load(export(gold, tmp_path / "site"), "municipios_top")["dados"]
    assert [r["posicao"] for r in rows] == list(range(1, len(rows) + 1))
    counts = [r["estabelecimentos_ativos"] for r in rows]
    assert counts == sorted(counts, reverse=True)
    monkeypatch.setattr(site_export, "TOP_MUNICIPIOS", 1)
    one_row = load(export(gold, tmp_path / "site2"), "municipios_top")["dados"]
    assert len(one_row) == 1
    assert one_row[0]["posicao"] == 1
    assert one_row[0]["estabelecimentos_ativos"] == counts[0]


# --------------------------------------------------------------------------- #
# Supressao
# --------------------------------------------------------------------------- #


def test_default_threshold_drops_small_cells_and_reports_how_many(gold, tmp_path):
    target = export(gold, tmp_path / "site", min_cell=site_export.MIN_CELL)
    uf = load(target, "uf")
    assert uf["dados"] == []
    assert uf["celulas_suprimidas"] > 0
    by_year = load(target, "aberturas_ano")
    assert by_year["dados"] == []
    assert by_year["celulas_suprimidas"] > 0


def test_threshold_one_keeps_everything_and_reports_zero(gold, tmp_path):
    target = export(gold, tmp_path / "site", min_cell=1)
    for name in ("uf", "aberturas_ano", "aberturas_mes", "ativos_secao", "socios"):
        assert load(target, name)["celulas_suprimidas"] == 0, name


def test_the_threshold_is_applied_per_cell_not_per_file(gold, tmp_path):
    # no teste, SP tem 1 empresa ativa: com limite 1 permanece; com 2 sai
    keep = {r["uf"] for r in load(export(gold, tmp_path / "a", min_cell=1), "uf")["dados"]}
    drop = {r["uf"] for r in load(export(gold, tmp_path / "b", min_cell=2), "uf")["dados"]}
    assert "SP" in keep
    assert "SP" not in drop


def test_invalid_threshold_is_rejected(gold):
    with pytest.raises(ExportError, match="min_cell"):
        build_documents(gold, MONTH, min_cell=0)


# --------------------------------------------------------------------------- #
# Meses parciais
# --------------------------------------------------------------------------- #


def test_current_year_and_month_are_marked_partial(tmp_path, monkeypatch):
    extra = {
        "cnpj_basico": "00000009", "cnpj_ordem": "0001", "cnpj_dv": "99",
        "identificador_matriz_filial": "1", "situacao_cadastral": "02",
        "data_inicio_atividade": "20261005", "cnae_fiscal_principal": "4711301",
        "uf": "SP", "municipio": "7107",
    }  # fmt: skip
    monkeypatch.setattr(test_gold, "ESTABELECIMENTOS", [*test_gold.ESTABELECIMENTOS, extra])
    build_silver(tmp_path)
    assert run_gold(tmp_path) == 0
    target = export(tmp_path / "gold", tmp_path / "site")
    by_year = {r["ano"]: r for r in load(target, "aberturas_ano")["dados"]}
    assert by_year[2026]["parcial"] is True
    assert by_year[2020]["parcial"] is False
    by_month = {r["ano_mes"]: r for r in load(target, "aberturas_mes")["dados"]}
    assert by_month["2026-10"]["parcial"] is True
    assert by_month["2020-01"]["parcial"] is False
    assert load(target, "aberturas_ano")["ano_parcial"] == 2026
    assert load(target, "aberturas_mes")["mes_parcial"] == "2026-10"


def test_series_start_years_are_respected(gold, tmp_path, monkeypatch):
    monkeypatch.setattr(site_export, "MONTHLY_START_YEAR", 2020)
    monkeypatch.setattr(site_export, "ANNUAL_START_YEAR", 2019)
    target = export(gold, tmp_path / "site")
    assert min(r["ano"] for r in load(target, "aberturas_ano")["dados"]) >= 2019
    assert min(r["ano_mes"] for r in load(target, "aberturas_mes")["dados"]) >= "2020-01"
    assert min(r["ano"] for r in load(target, "aberturas_secao_ano")["dados"]) >= 2020
    overview = load(target, "overview")["dados"]
    assert overview["aberturas_antes_do_inicio_da_serie"] > 0


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


def test_pipeline_file_mirrors_the_gold_manifest(gold, tmp_path):
    manifest = json.loads(manifest_path(gold).read_text(encoding="utf-8"))
    doc = load(export(gold, tmp_path / "site"), "pipeline")["dados"]
    assert {m["nome"] for m in doc["gold"]["modelos"]} == set(manifest["models"])
    for m in doc["gold"]["modelos"]:
        assert m["linhas"] == manifest["models"][m["nome"]]["rows"]
    assert len(doc["gold"]["reconciliacoes"]) == len(manifest["checks"])
    assert all(c["ok"] for c in doc["gold"]["reconciliacoes"])
    assert "bronze" not in doc  # sem manifesto da bronze neste teste


def test_pipeline_file_includes_silver_when_its_manifest_exists(gold, tmp_path):
    doc = load(export(gold, tmp_path / "site"), "pipeline")["dados"]
    assert {t["nome"] for t in doc["silver"]["tabelas"]} >= {"Estabelecimentos", "Empresas"}
    for t in doc["silver"]["tabelas"]:
        assert t["linhas_saida"] <= t["linhas_entrada"]


# --------------------------------------------------------------------------- #
# Recusas
# --------------------------------------------------------------------------- #


def test_refuses_when_a_blocking_check_failed(gold, tmp_path):
    path = manifest_path(gold)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["checks"][0]["ok"] = False
    data["checks"][0]["blocking"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "site"
    with pytest.raises(ExportError, match=data["checks"][0]["name"]):
        export(gold, out)
    assert not out.exists()


def test_a_failed_non_blocking_check_does_not_stop_the_export(gold, tmp_path):
    path = manifest_path(gold)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["checks"][0]["ok"] = False
    data["checks"][0]["blocking"] = False
    path.write_text(json.dumps(data), encoding="utf-8")
    export(gold, tmp_path / "site")


def test_refuses_without_a_manifest(gold, tmp_path):
    manifest_path(gold).unlink()
    with pytest.raises(ExportError, match="manifesto"):
        export(gold, tmp_path / "site")


def test_refuses_a_manifest_from_another_month(gold, tmp_path):
    path = manifest_path(gold)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["reference_month"] = "2026-09"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ExportError, match="outro mes"):
        export(gold, tmp_path / "site")


def test_refuses_a_manifest_without_checks(gold, tmp_path):
    path = manifest_path(gold)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["checks"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ExportError, match="reconciliacoes"):
        export(gold, tmp_path / "site")


def test_refuses_when_a_mart_is_missing(gold, tmp_path):
    gold_path(gold, "mart_socios_qualificacao", MONTH).unlink()
    with pytest.raises(ExportError, match="mart_socios_qualificacao"):
        export(gold, tmp_path / "site")


def test_refuses_files_above_the_size_cap_and_writes_nothing(gold, tmp_path, monkeypatch):
    monkeypatch.setattr(site_export, "MAX_FILE_BYTES", 200)
    out = tmp_path / "site"
    with pytest.raises(ExportError, match="limite"):
        export(gold, out)
    assert not out.exists()


def test_refuses_a_key_outside_the_allowlist(gold, tmp_path, monkeypatch):
    monkeypatch.setattr(site_export, "ALLOWED_KEYS", frozenset(ALLOWED_KEYS - {"uf_nome"}))
    with pytest.raises(ExportError, match="uf_nome"):
        export(gold, tmp_path / "site")


def test_documents_are_checked_for_dynamic_objects(gold, monkeypatch):
    def boom(name, value):
        raise ExportError("verificado")

    monkeypatch.setattr(site_export, "check_dynamic", boom)
    with pytest.raises(ExportError, match="verificado"):
        build_documents(gold, MONTH, min_cell=1)


def test_refuses_unknown_caveats_and_non_numeric_details():
    with pytest.raises(ExportError, match="ressalva"):
        site_export.check_dynamic("x", {"ressalvas": {"inventada": "t"}})
    with pytest.raises(ExportError, match="numeros"):
        site_export.check_dynamic("x", {"detalhe": {"a": "texto"}})
    with pytest.raises(ExportError, match="numeros"):
        site_export.check_dynamic("x", {"etapas": {"a": True}})
    site_export.check_dynamic("x", {"detalhe": {"a": 1}, "etapas": {"b": 2.5}})


# --------------------------------------------------------------------------- #
# Diretorio de saida e CLI
# --------------------------------------------------------------------------- #


def test_stale_files_are_removed_from_the_month_directory(gold, tmp_path):
    out = tmp_path / "site"
    stale = out / MONTH / "antigo.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    export(gold, out)
    assert not stale.exists()


def test_months_index_lists_complete_months_in_order(gold, tmp_path):
    out = tmp_path / "site"
    export(gold, out)
    (out / "2026-09").mkdir()
    (out / "2026-09" / "index.json").write_text("{}", encoding="utf-8")
    (out / "2026-08").mkdir()  # sem index.json: incompleto, nao entra
    (out / "docs").mkdir()  # nao e um mes
    site_export.write_months_index(out)
    months = json.loads((out / "months.json").read_text(encoding="utf-8"))
    assert months["meses"] == ["2026-09", MONTH]
    assert months["ultimo"] == MONTH


def test_render_matches_what_is_written(gold, tmp_path):
    documents = build_documents(gold, MONTH, min_cell=1)
    texts = render(documents, MONTH)
    target = export(gold, tmp_path / "site")
    for filename, text in texts.items():
        assert (target / filename).read_text(encoding="utf-8") == text


def test_cli_success_and_failures(gold, tmp_path, capsys):
    out = tmp_path / "site"
    assert main(["--month", MONTH, "--gold-dir", str(gold), "--out", str(out), "--min-cell", "1"]) == 0
    assert "Arquivos gravados" in capsys.readouterr().out
    assert (out / MONTH / "index.json").exists()
    assert main(["--month", "2026-13x", "--gold-dir", str(gold), "--out", str(out)]) == 2
    assert main(["--month", "2026-09", "--gold-dir", str(gold), "--out", str(out)]) == 1
    assert "recusada" in capsys.readouterr().err


def test_cli_default_threshold_is_the_module_constant(gold, tmp_path):
    out = tmp_path / "site"
    assert main(["--month", MONTH, "--gold-dir", str(gold), "--out", str(out)]) == 0
    assert load(out / MONTH, "uf")["dados"] == []


def test_the_module_does_not_import_the_agent_or_the_cloud_code():
    source = Path(site_export.__file__).read_text(encoding="utf-8")
    assert "cnpj_pipeline.agent" not in source
    assert "cnpj_pipeline.cloud" not in source
    assert "fato_estabelecimento" not in source.split("SOURCES =")[1].split("UF_NOMES")[0]
