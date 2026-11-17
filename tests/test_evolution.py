from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import duckdb
import pytest

from cnpj_pipeline import evolution
from cnpj_pipeline.evolution import (
    EvolutionError,
    build_month,
    main,
    previous_pairs,
    render_report,
    run_checks,
)
from cnpj_pipeline.gold import gold_path

ATIVA, SUSPENSA, INAPTA, BAIXADA = "02", "03", "04", "08"


def row(cnpj, situacao=ATIVA, *, uf="SP", secao="G", politica=False, matriz=True, inicio=date(2020, 1, 15)):
    return (cnpj, matriz, situacao, situacao == ATIVA, uf, secao, politica, inicio)


def write_month(gold_dir: Path, month: str, rows: list[tuple], *, approved: bool = True) -> None:
    path = gold_path(gold_dir, "fato_estabelecimento", month)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE f (cnpj VARCHAR, is_matriz BOOLEAN, situacao_cadastral VARCHAR, is_ativo BOOLEAN, "
        "uf VARCHAR, cnae_secao VARCHAR, is_organizacao_politica BOOLEAN, data_inicio_atividade DATE)"
    )
    if rows:
        con.executemany("INSERT INTO f VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.execute(f"COPY f TO '{path.as_posix()}' (FORMAT parquet)")
    con.close()
    checks = [{"name": "chave_unica", "ok": approved, "blocking": True, "detail": {}, "description": ""}]
    (gold_dir / f"_manifest_{month}.json").write_text(
        json.dumps({"reference_month": month, "checks": checks}), encoding="utf-8"
    )


def build(gold_dir: Path, tmp_path: Path, month="2026-09", previous="2026-08", **kwargs):
    options = {
        "memory_limit": "1GB",
        "threads": 1,
        "tmp_root": tmp_path / "tmp",
        "report_dir": None,
        "force": False,
    }
    options.update(kwargs)
    return build_month(gold_dir, month, previous, **options)


def mart(gold_dir: Path, month="2026-09") -> list[dict]:
    path = gold_path(gold_dir, "mart_movimentos", month)
    con = duckdb.connect()
    cur = con.execute(f"SELECT * FROM read_parquet('{path.as_posix()}') ORDER BY ALL")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def cell(rows: list[dict], **where) -> int:
    return sum(r["estabelecimentos"] for r in rows if all(r[k] == v for k, v in where.items()))


@pytest.fixture
def gold(tmp_path):
    gold_dir = tmp_path / "gold"
    write_month(
        gold_dir,
        "2026-08",
        [
            row("00000000000101"),  # continua ativo
            row("00000000000102"),  # vai ser baixado
            row("00000000000103", SUSPENSA),  # vai voltar a ser ativo
            row("00000000000104", uf="RJ", secao="C", politica=True),  # vai sumir
            row("00000000000105", BAIXADA),  # continua baixado
        ],
    )
    write_month(
        gold_dir,
        "2026-09",
        [
            row("00000000000101"),
            row("00000000000102", BAIXADA),
            row("00000000000103", ATIVA),
            row("00000000000105", BAIXADA),
            row("00000000000201", uf="MG", secao="H", inicio=date(2026, 9, 2)),  # novo, no mes
            row("00000000000202", uf="MG", secao="H", inicio=date(2026, 8, 20)),  # novo, mes anterior
            row("00000000000203", INAPTA, uf="MG", secao="H", inicio=date(2022, 3, 1)),  # novo, atrasado
            row("00000000000204", uf="PR", secao="G", inicio=None),  # novo, sem data
        ],
    )
    return gold_dir


def test_each_kind_of_movement_is_counted_and_unchanged_establishments_are_ignored(gold, tmp_path):
    build(gold, tmp_path)
    rows = mart(gold)
    assert cell(rows, movimento="novo") == 4
    assert cell(rows, movimento="removido") == 1
    assert cell(rows, movimento="mudou_situacao") == 2
    assert cell(rows, movimento="mudou_situacao", situacao_anterior=ATIVA, situacao_atual=BAIXADA) == 1
    assert cell(rows, movimento="mudou_situacao", situacao_anterior=SUSPENSA, situacao_atual=ATIVA) == 1
    assert sum(r["estabelecimentos"] for r in rows) == 7  # 101 e 105 nao entram


def test_a_removed_establishment_keeps_the_attributes_it_had_in_the_previous_snapshot(gold, tmp_path):
    build(gold, tmp_path)
    removed = [r for r in mart(gold) if r["movimento"] == "removido"]
    assert len(removed) == 1
    assert (removed[0]["uf"], removed[0]["cnae_secao"], removed[0]["is_organizacao_politica"]) == (
        "RJ",
        "C",
        True,
    )
    assert removed[0]["situacao_anterior"] == ATIVA and removed[0]["situacao_atual"] is None


def test_new_establishments_are_split_by_how_late_they_were_registered(gold, tmp_path):
    build(gold, tmp_path)
    rows = mart(gold)
    assert cell(rows, movimento="novo", defasagem_inicio="ate_1_mes") == 2  # setembro e agosto
    assert cell(rows, movimento="novo", defasagem_inicio="mais_de_12_meses") == 1
    assert cell(rows, movimento="novo", defasagem_inicio="sem_data") == 1
    assert all(r["defasagem_inicio"] is None for r in rows if r["movimento"] != "novo")


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (date(2026, 9, 30), "ate_1_mes"),
        (date(2026, 8, 1), "ate_1_mes"),
        (date(2026, 7, 31), "2_a_3_meses"),
        (date(2026, 6, 1), "2_a_3_meses"),
        (date(2026, 5, 31), "4_a_12_meses"),
        (date(2025, 9, 1), "4_a_12_meses"),
        (date(2025, 8, 31), "mais_de_12_meses"),
        (date(2026, 10, 1), "data_futura"),
        (None, "sem_data"),
    ],
)
def test_the_lag_buckets_follow_the_month_of_the_snapshot(tmp_path, start, expected):
    gold_dir = tmp_path / "gold"
    write_month(gold_dir, "2026-08", [row("00000000000101")])
    write_month(gold_dir, "2026-09", [row("00000000000101"), row("00000000000301", inicio=start)])
    build(gold_dir, tmp_path)
    assert [r["defasagem_inicio"] for r in mart(gold_dir) if r["movimento"] == "novo"] == [expected]


def test_the_mart_has_counts_only_and_no_cnpj(gold, tmp_path):
    build(gold, tmp_path)
    assert not any("cnpj" in name for name in mart(gold)[0])
    assert not re.search(
        r"\d{14}", gold_path(gold, "mart_movimentos", "2026-09").read_bytes().decode("latin-1")
    )


def test_the_arithmetic_checks_pass_and_are_blocking(gold, tmp_path):
    manifest = build(gold, tmp_path)
    assert [c["name"] for c in manifest["checks"]] == [
        "movimentos_fecham_o_total",
        "movimentos_fecham_os_ativos",
    ]
    assert all(c["ok"] and c["blocking"] for c in manifest["checks"])
    summary = manifest["summary"]
    assert (summary["novos"], summary["removidos"], summary["mudaram_de_situacao"]) == (4, 1, 2)
    assert (summary["novos_ativos"], summary["removidos_ativos"]) == (3, 1)
    assert (summary["passaram_a_ativos"], summary["deixaram_de_ser_ativos"]) == (1, 1)


def test_a_wrong_mart_is_caught_by_the_arithmetic_checks(gold, tmp_path):
    build(gold, tmp_path)
    path = gold_path(gold, "mart_movimentos", "2026-09")
    con = duckdb.connect()
    wrong = (tmp_path / "wrong.parquet").as_posix()
    con.execute(
        "COPY (SELECT * REPLACE (CASE WHEN movimento = 'novo' THEN estabelecimentos + 1 "
        f"ELSE estabelecimentos END AS estabelecimentos) FROM read_parquet('{path.as_posix()}')) "
        f"TO '{wrong}' (FORMAT parquet)"
    )
    checks = run_checks(
        con,
        gold_path(gold, "fato_estabelecimento", "2026-09"),
        gold_path(gold, "fato_estabelecimento", "2026-08"),
        tmp_path / "wrong.parquet",
    )
    assert [c.ok for c in checks] == [False, False]


def test_the_net_change_of_active_establishments_is_the_difference_between_the_two_snapshots(gold, tmp_path):
    summary = build(gold, tmp_path)["summary"]
    active_august = 3  # 101, 102 e 104
    active_september = 5  # 101, 103, 201, 202 e 204
    net = (
        summary["novos_ativos"]
        - summary["removidos_ativos"]
        + summary["passaram_a_ativos"]
        - summary["deixaram_de_ser_ativos"]
    )
    assert net == active_september - active_august == 2
    by_uf = {r["uf"]: r["saldo"] for r in summary["saldo_ativos_por_uf"]}
    assert by_uf == {"MG": 2, "PR": 1, "SP": 0, "RJ": -1}  # o saldo por UF soma o saldo nacional
    assert sum(by_uf.values()) == net


def test_without_an_approved_gold_nothing_is_built(tmp_path):
    gold_dir = tmp_path / "gold"
    write_month(gold_dir, "2026-08", [row("00000000000101")])
    with pytest.raises(EvolutionError, match="fato da gold ausente"):
        build(gold_dir, tmp_path)
    write_month(gold_dir, "2026-09", [row("00000000000101")], approved=False)
    with pytest.raises(EvolutionError, match="nao esta aprovada"):
        build(gold_dir, tmp_path)
    assert not gold_path(gold_dir, "mart_movimentos", "2026-09").exists()


def test_an_existing_mart_is_kept_unless_forced(gold, tmp_path):
    assert build(gold, tmp_path) is not None
    assert build(gold, tmp_path) is None
    assert build(gold, tmp_path, force=True) is not None


def test_months_are_paired_in_order_and_validated():
    assert previous_pairs(["2026-09", "2026-07", "2026-08"]) == [
        ("2026-07", "2026-08"),
        ("2026-08", "2026-09"),
    ]
    for months, message in [
        (["2026-07"], "pelo menos dois"),
        (["2026-07", "2026-07"], "repetido"),
        (["2026-07", "2026-13"], "invalido"),
    ]:
        with pytest.raises(EvolutionError, match=message):
            previous_pairs(months)


def test_the_report_has_counts_and_labels_and_no_cnpj(gold, tmp_path):
    manifest = build(gold, tmp_path)
    text = render_report("2026-09", "2026-08", manifest)
    assert "# Evolução do cadastro: de 2026-08 para 2026-09" in text
    assert "Suspensa" in text and "Baixada" in text and "mais de 12 meses" in text
    assert "Variação líquida de estabelecimentos ativos: +2." in text
    assert not re.search(r"\d{14}", text)


def test_main_writes_the_mart_manifest_and_report_and_returns_zero(gold, tmp_path, monkeypatch):
    monkeypatch.setattr(evolution.config, "DATA_DIR", tmp_path)
    report_dir = tmp_path / "docs"
    code = main(["--months", "2026-08", "2026-09", "--gold-dir", str(gold), "--report-dir", str(report_dir)])
    assert code == 0
    assert (report_dir / "2026-09.md").exists()
    saved = json.loads((gold / "_evolution_2026-09.json").read_text(encoding="utf-8"))
    assert saved["previous_month"] == "2026-08" and saved["rows"] > 0


def test_main_refuses_with_exit_code_one_when_the_gold_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(evolution.config, "DATA_DIR", tmp_path)
    assert main(["--months", "2026-08", "2026-09", "--gold-dir", str(tmp_path / "gold")]) == 1
    assert "Evolucao recusada" in capsys.readouterr().err
