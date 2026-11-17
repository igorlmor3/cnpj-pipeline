from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from cnpj_pipeline import site_evolution
from cnpj_pipeline.gold import gold_path
from cnpj_pipeline.site_evolution import (
    ALLOWED_KEYS,
    EVOLUTION_KEYS,
    ExportError,
    build_document,
    collect_keys,
    main,
    run,
)

MONTHS = ["2026-08", "2026-09", "2026-10"]

# (uf, estabelecimentos_ativos, empresas_ativas, optantes_simples, optantes_mei) por mes
STOCK = {
    "2026-08": [("SP", 100, 80, 60, 40), ("MG", 50, 40, 30, 20)],
    "2026-09": [("SP", 105, 82, 62, 41), ("MG", 48, 39, 29, 20)],
    "2026-10": [("SP", 110, 85, 64, 43), ("MG", 48, 39, 29, 20)],
}

# (movimento, anterior, atual, uf, secao, politica, faixa, estabelecimentos)
MOVEMENTS = {
    "2026-09": [
        ("novo", None, "02", "SP", "G", False, "ate_1_mes", 8),
        ("novo", None, "02", "SP", "G", False, "mais_de_12_meses", 2),
        ("novo", None, "08", "MG", "H", False, "ate_1_mes", 3),
        ("removido", "02", None, "MG", "H", False, None, 1),
        ("mudou_situacao", "02", "08", "SP", "G", False, None, 5),
        ("mudou_situacao", "03", "02", "MG", "H", False, None, 2),
        ("mudou_situacao", "02", "08", "MG", "H", False, None, 3),
        ("mudou_situacao", "03", "04", "SP", "G", False, None, 2),
    ],
    "2026-10": [("novo", None, "02", "SP", "G", False, "ate_1_mes", 5)],
}


def write_parquet(path: Path, create: str, insert: str, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t ({create})")
    if rows:
        con.executemany(f"INSERT INTO t VALUES ({insert})", rows)
    con.execute(f"COPY t TO '{path.as_posix()}' (FORMAT parquet)")
    con.close()


def write_gold(gold_dir: Path, months=MONTHS, *, stock=STOCK, movements=MOVEMENTS, approved=True) -> None:
    for month in months:
        write_parquet(
            gold_path(gold_dir, "mart_estabelecimentos_ativos", month),
            "uf VARCHAR, estabelecimentos_ativos BIGINT",
            "?, ?",
            [(uf, ativos) for uf, ativos, *_ in stock[month]],
        )
        write_parquet(
            gold_path(gold_dir, "mart_simples_mei_uf", month),
            "uf VARCHAR, is_organizacao_politica BOOLEAN, empresas_ativas BIGINT, optantes_simples BIGINT, "
            "optantes_mei BIGINT",
            "?, FALSE, ?, ?, ?",
            [(uf, emp, simples, mei) for uf, _, emp, simples, mei in stock[month]],
        )
        if month in movements:
            previous = months[months.index(month) - 1]
            write_parquet(
                gold_path(gold_dir, "mart_movimentos", month),
                "mes_anterior VARCHAR, movimento VARCHAR, situacao_anterior VARCHAR, situacao_atual VARCHAR, "
                "uf VARCHAR, cnae_secao VARCHAR, is_organizacao_politica BOOLEAN, defasagem_inicio VARCHAR, "
                "estabelecimentos BIGINT",
                f"'{previous}', ?, ?, ?, ?, ?, ?, ?, ?",
                list(movements[month]),
            )
        checks = [{"name": "chave_unica", "ok": approved, "blocking": True, "detail": {}, "description": ""}]
        (gold_dir / f"_manifest_{month}.json").write_text(
            json.dumps({"reference_month": month, "checks": checks}), encoding="utf-8"
        )


@pytest.fixture
def gold(tmp_path):
    gold_dir = tmp_path / "gold"
    write_gold(gold_dir)
    return gold_dir


def test_the_stock_series_has_one_point_per_month_in_order(gold):
    doc = build_document(gold, ["2026-10", "2026-08", "2026-09"])
    assert doc["meses"] == MONTHS
    serie = doc["dados"]["serie"]
    assert [s["mes"] for s in serie] == MONTHS
    assert [s["estabelecimentos_ativos"] for s in serie] == [150, 153, 158]
    assert serie[0]["empresas_ativas"] == 120 and serie[0]["optantes_simples"] == 90
    assert serie[0]["pct_simples"] == 75.0 and serie[0]["pct_mei"] == 50.0


def test_the_stock_by_uf_lists_every_uf_of_every_month(gold):
    by_uf = build_document(gold, MONTHS)["dados"]["serie_uf"]
    assert len(by_uf) == 6
    sp = {r["mes"]: r["estabelecimentos_ativos"] for r in by_uf if r["uf"] == "SP"}
    assert sp == {"2026-08": 100, "2026-09": 105, "2026-10": 110}
    assert {r["uf_nome"] for r in by_uf} == {"São Paulo", "Minas Gerais"}


def test_the_movements_of_each_month_compare_it_with_the_previous_one(gold):
    movimentos = build_document(gold, MONTHS)["dados"]["movimentos"]
    assert [(m["mes_anterior"], m["mes"]) for m in movimentos] == [
        ("2026-08", "2026-09"),
        ("2026-09", "2026-10"),
    ]
    september = movimentos[0]
    assert (september["novos"], september["novos_ativos"]) == (13, 10)
    assert (september["removidos"], september["removidos_ativos"]) == (1, 1)
    assert (september["mudaram_de_situacao"], september["passaram_a_ativos"]) == (12, 2)
    assert september["deixaram_de_ser_ativos"] == 8
    assert september["saldo_ativos"] == 3 == 153 - 150


def test_the_net_change_by_uf_adds_up_to_the_national_net_change(gold):
    doc = build_document(gold, MONTHS)
    september = {r["uf"]: r["saldo"] for r in doc["dados"]["saldo_uf"] if r["mes"] == "2026-09"}
    assert september == {"MG": -2, "SP": 5}
    assert sum(september.values()) == doc["dados"]["movimentos"][0]["saldo_ativos"]


def test_the_lag_of_new_establishments_is_listed_in_the_fixed_order_with_labels(gold):
    lag = [r for r in build_document(gold, MONTHS)["dados"]["defasagem"] if r["mes"] == "2026-09"]
    assert [(r["faixa"], r["novos"]) for r in lag] == [("ate_1_mes", 11), ("mais_de_12_meses", 2)]
    assert lag[1]["faixa_descricao"] == "mais de 12 meses"


def test_small_transitions_are_dropped_and_counted_but_the_totals_keep_them(gold):
    doc = build_document(gold, MONTHS)
    transitions = [t for t in doc["dados"]["transicoes"] if t["mes"] == "2026-09"]
    assert [(t["de"], t["para"], t["estabelecimentos"]) for t in transitions] == [("02", "08", 8)]
    assert transitions[0]["de_descricao"] == "Ativa" and transitions[0]["para_descricao"] == "Baixada"
    assert doc["celulas_suprimidas"] == 2  # 03->02 (2) e 03->04 (2)
    assert doc["dados"]["movimentos"][0]["mudaram_de_situacao"] == 12  # os totais nao perdem nada


def test_a_lower_min_cell_keeps_more_transitions(gold):
    doc = build_document(gold, MONTHS, min_cell=2)
    assert len([t for t in doc["dados"]["transicoes"] if t["mes"] == "2026-09"]) == 3
    assert doc["celulas_suprimidas"] == 0


def test_a_net_change_that_does_not_match_the_two_stocks_is_refused(tmp_path):
    stock = {**STOCK, "2026-09": [("SP", 105, 82, 62, 41), ("MG", 40, 39, 29, 20)]}  # 145 em vez de 153
    write_gold(tmp_path / "gold", stock=stock)
    with pytest.raises(ExportError, match="nao fecha com a diferenca entre os estoques"):
        build_document(tmp_path / "gold", MONTHS)


def test_movements_that_compare_with_another_month_are_refused(gold):
    path = gold_path(gold, "mart_movimentos", "2026-09")
    other = gold / "other.parquet"
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT * REPLACE ('2026-07' AS mes_anterior) FROM read_parquet('{path.as_posix()}')) "
        f"TO '{other.as_posix()}' (FORMAT parquet)"
    )
    con.close()
    other.replace(path)
    with pytest.raises(ExportError, match="comparam com 2026-07, mas o mes anterior da lista e 2026-08"):
        build_document(gold, MONTHS)


def test_missing_movements_point_to_the_command_that_builds_them(tmp_path):
    write_gold(tmp_path / "gold", movements={"2026-10": MOVEMENTS["2026-10"]})
    with pytest.raises(ExportError, match=r"faltam os movimentos de 2026-09.*cnpj_pipeline.evolution"):
        build_document(tmp_path / "gold", MONTHS)


def test_a_gold_that_was_not_approved_is_refused(tmp_path):
    write_gold(tmp_path / "gold", approved=False)
    with pytest.raises(ExportError, match="reconciliacoes bloqueantes"):
        build_document(tmp_path / "gold", MONTHS)


@pytest.mark.parametrize(
    ("months", "message"),
    [
        (["2026-08"], "pelo menos dois"),
        (["2026-08", "2026-08"], "repetido"),
        (["2026-08", "2026-13"], "invalido"),
    ],
)
def test_the_month_list_is_validated(gold, months, message):
    with pytest.raises(ExportError, match=message):
        build_document(gold, months)


def test_the_export_never_needs_the_per_cnpj_fact(gold):
    assert not list(gold.glob("fato_estabelecimento*"))
    assert build_document(gold, MONTHS)["dados"]["serie"]


def test_every_key_is_allowed_and_every_new_key_is_used(gold):
    used = collect_keys(build_document(gold, MONTHS))
    assert used <= ALLOWED_KEYS | EVOLUTION_KEYS
    assert EVOLUTION_KEYS - used == set()


def test_a_key_outside_the_list_makes_the_export_fail(gold, monkeypatch):
    monkeypatch.setattr(site_evolution, "EVOLUTION_KEYS", frozenset(EVOLUTION_KEYS - {"saldo_uf"}))
    with pytest.raises(ExportError, match="chaves fora da lista permitida"):
        build_document(gold, MONTHS)


def test_the_file_is_the_same_byte_for_byte_every_time(gold, tmp_path):
    first = run(gold, MONTHS, tmp_path / "a").read_bytes()
    second = run(gold, MONTHS, tmp_path / "b").read_bytes()
    assert first == second and first.endswith(b"\n") and b"\r" not in first
    assert len(first) < site_evolution.MAX_FILE_BYTES


def test_main_writes_the_file_and_returns_zero(gold, tmp_path, capsys):
    out = tmp_path / "site"
    assert main(["--months", *MONTHS, "--gold-dir", str(gold), "--out", str(out)]) == 0
    assert json.loads((out / "evolucao.json").read_text(encoding="utf-8"))["arquivo"] == "evolucao"
    assert "evolucao.json" in capsys.readouterr().out


def test_main_refuses_with_exit_code_one(tmp_path, capsys):
    assert main(["--months", *MONTHS, "--gold-dir", str(tmp_path / "gold"), "--out", str(tmp_path)]) == 1
    assert "Exportacao recusada" in capsys.readouterr().err
