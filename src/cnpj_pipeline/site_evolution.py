"""Exporta a evolucao entre meses para o site: um unico JSON, `evolucao.json`.

Junta, mes a mes, o que cada foto mostra (estoque de estabelecimentos e empresas ativas, por UF) e o que
mudou de uma foto para a seguinte (novos, removidos, mudancas de situacao, defasagem dos CNPJs novos).
As mesmas regras de `site_export` valem aqui:

- le somente marts agregados da gold, nunca `fato_estabelecimento` (os movimentos ja vem agregados de
  `cnpj_pipeline.evolution`);
- recusa-se a rodar se a gold de algum mes nao foi aprovada;
- descarta recortes com contagem abaixo de MIN_CELL e informa quantos foram descartados;
- confere que o saldo de ativos dos movimentos fecha com a diferenca entre os estoques de dois meses;
- a saida nao tem data nem hora: rodar duas vezes gera arquivos identicos byte a byte.

Uso:
    python -m cnpj_pipeline.site_evolution --months 2026-07 2026-08 2026-09 2026-10 --out site/public/data
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import duckdb

from cnpj_pipeline import config
from cnpj_pipeline.download import MONTH_RE
from cnpj_pipeline.evolution import LAG_LABELS, LAG_ORDER
from cnpj_pipeline.evolution import MODEL as MOVEMENTS
from cnpj_pipeline.gold import gold_path
from cnpj_pipeline.gold_models import SITUACAO_ATIVA, SITUACOES_CADASTRAIS
from cnpj_pipeline.site_export import (
    ALLOWED_KEYS,
    MAX_FILE_BYTES,
    MIN_CELL,
    SCHEMA_VERSION,
    UF_NOMES,
    ExportError,
    _q,
    check_gold,
    collect_keys,
    dumps,
    one,
    pct,
    read_manifest,
    rows,
)

log = logging.getLogger("cnpj_pipeline.site_evolution")

FILENAME = "evolucao.json"
TOP_TRANSICOES = 6

RESSALVAS_EVOLUCAO: dict[str, str] = {
    "fotos_mensais": (
        "Cada ponto é uma foto completa do cadastro em um mês. Os movimentos comparam duas fotos "
        "consecutivas e não mostram o que aconteceu dentro do intervalo."
    ),
    "cadastro_retroativo": (
        "CNPJs novos podem trazer data de início de atividade anterior ao mês da foto: o cadastro "
        "registra com atraso. A defasagem mostra quanto do que aparece como novo é, na verdade, retroativo."
    ),
    "celulas_pequenas": (
        f"Transições com menos de {MIN_CELL} estabelecimentos não são publicadas. O número de recortes "
        "omitidos aparece no arquivo."
    ),
}

TITULO = "Evolução do cadastro entre meses"
DESCRICAO = (
    "Estoque de estabelecimentos e empresas ativas em cada foto mensal e os movimentos entre fotos "
    "consecutivas: novos, removidos e mudanças de situação."
)

# Chaves que so existem neste arquivo. Um teste garante que todas sao usadas.
EVOLUTION_KEYS = frozenset(
    {
        "mes", "mes_anterior", "serie", "serie_uf", "movimentos", "transicoes", "defasagem", "saldo_uf",
        "novos", "novos_ativos", "removidos", "removidos_ativos", "mudaram_de_situacao",
        "passaram_a_ativos", "deixaram_de_ser_ativos", "saldo_ativos", "de", "para", "de_descricao",
        "para_descricao", "faixa", "faixa_descricao", "saldo", "estabelecimentos",
    }
)  # fmt: skip


def _read(path: Path) -> str:
    return f"read_parquet({_q(path.as_posix())}, hive_partitioning=false)"


def _situacao(code: str) -> str:
    return dict(SITUACOES_CADASTRAIS).get(code, f"código {code}")


def validate_months(months: list[str]) -> list[str]:
    if len(months) < 2:
        raise ExportError("informe pelo menos dois meses")
    if len(set(months)) != len(months):
        raise ExportError("mes repetido na lista")
    for month in months:
        if not MONTH_RE.match(month):
            raise ExportError(f"mes invalido: {month!r} (formato AAAA-MM)")
    return sorted(months)


def _mart(con: duckdb.DuckDBPyConnection, gold_dir: Path, model: str, month: str) -> str:
    path = gold_path(gold_dir, model, month)
    if not path.exists():
        raise ExportError(f"mart ausente na gold: {path}")
    return _read(path)


def month_stock(con: duckdb.DuckDBPyConnection, gold_dir: Path, month: str) -> tuple[dict, list[dict]]:
    ativos = _mart(con, gold_dir, "mart_estabelecimentos_ativos", month)
    simples = _mart(con, gold_dir, "mart_simples_mei_uf", month)
    estabelecimentos = one(con, f"SELECT coalesce(sum(estabelecimentos_ativos), 0) AS n FROM {ativos}")["n"]
    empresas = one(
        con,
        f"""
        SELECT
            coalesce(sum(empresas_ativas), 0) AS empresas,
            coalesce(sum(empresas_ativas) FILTER (WHERE is_organizacao_politica), 0) AS pol,
            coalesce(sum(optantes_simples), 0) AS simples,
            coalesce(sum(optantes_mei), 0) AS mei
        FROM {simples}
        """,
    )
    serie = {
        "mes": month,
        "estabelecimentos_ativos": int(estabelecimentos),
        "empresas_ativas": int(empresas["empresas"]),
        "optantes_simples": int(empresas["simples"]),
        "optantes_mei": int(empresas["mei"]),
        "pct_simples": pct(empresas["simples"], empresas["empresas"]),
        "pct_mei": pct(empresas["mei"], empresas["empresas"]),
    }
    por_uf = rows(
        con,
        f"""
        SELECT a.uf, a.n AS estabelecimentos_ativos, coalesce(s.n, 0) AS empresas_ativas
        FROM (SELECT uf, sum(estabelecimentos_ativos) AS n FROM {ativos} WHERE uf IS NOT NULL GROUP BY uf) a
        LEFT JOIN (SELECT uf, sum(empresas_ativas) AS n FROM {simples} GROUP BY uf) s ON s.uf = a.uf
        ORDER BY a.uf
        """,
    )
    serie_uf = [
        {
            "mes": month,
            "uf": r["uf"],
            "uf_nome": UF_NOMES.get(r["uf"], r["uf"]),
            "estabelecimentos_ativos": int(r["estabelecimentos_ativos"]),
            "empresas_ativas": int(r["empresas_ativas"]),
        }
        for r in por_uf
    ]
    return serie, serie_uf


def month_movements(
    con: duckdb.DuckDBPyConnection, gold_dir: Path, month: str, previous: str, min_cell: int
) -> tuple[dict, list[dict], list[dict], list[dict], int]:
    src = _mart(con, gold_dir, MOVEMENTS, month)
    declared = one(con, f"SELECT min(mes_anterior) AS a, max(mes_anterior) AS b FROM {src}")
    if declared["a"] != previous or declared["b"] != previous:
        raise ExportError(
            f"os movimentos de {month} comparam com {declared['a']}, mas o mes anterior da lista e {previous}"
        )
    ativa = _q(SITUACAO_ATIVA)

    def total(where: str) -> int:
        return int(one(con, f"SELECT coalesce(sum(estabelecimentos), 0) AS n FROM {src} WHERE {where}")["n"])

    resumo = {
        "mes": month,
        "mes_anterior": previous,
        "novos": total("movimento = 'novo'"),
        "novos_ativos": total(f"movimento = 'novo' AND situacao_atual = {ativa}"),
        "removidos": total("movimento = 'removido'"),
        "removidos_ativos": total(f"movimento = 'removido' AND situacao_anterior = {ativa}"),
        "mudaram_de_situacao": total("movimento = 'mudou_situacao'"),
        "passaram_a_ativos": total(f"movimento = 'mudou_situacao' AND situacao_atual = {ativa}"),
        "deixaram_de_ser_ativos": total(f"movimento = 'mudou_situacao' AND situacao_anterior = {ativa}"),
    }
    resumo["saldo_ativos"] = (
        resumo["novos_ativos"]
        - resumo["removidos_ativos"]
        + resumo["passaram_a_ativos"]
        - resumo["deixaram_de_ser_ativos"]
    )

    candidates = rows(
        con,
        f"""
        SELECT situacao_anterior AS de, situacao_atual AS para, sum(estabelecimentos) AS n FROM {src}
        WHERE movimento = 'mudou_situacao' GROUP BY 1, 2 ORDER BY n DESC, 1, 2
        """,
    )
    kept = [r for r in candidates if r["n"] >= min_cell][:TOP_TRANSICOES]
    omitted = len([r for r in candidates if r["n"] < min_cell])
    transicoes = [
        {
            "mes": month,
            "de": r["de"],
            "para": r["para"],
            "de_descricao": _situacao(r["de"]),
            "para_descricao": _situacao(r["para"]),
            "estabelecimentos": int(r["n"]),
        }
        for r in kept
    ]
    por_faixa = {
        r["faixa"]: int(r["n"])
        for r in rows(
            con,
            f"SELECT defasagem_inicio AS faixa, sum(estabelecimentos) AS n FROM {src} "
            "WHERE movimento = 'novo' GROUP BY 1",
        )
    }
    defasagem = [
        {"mes": month, "faixa": f, "faixa_descricao": LAG_LABELS[f], "novos": por_faixa[f]}
        for f in LAG_ORDER
        if por_faixa.get(f)
    ]
    saldo_uf = [
        {"mes": month, "uf": r["uf"], "uf_nome": UF_NOMES.get(r["uf"], r["uf"]), "saldo": int(r["saldo"])}
        for r in rows(
            con,
            f"""
            SELECT uf, sum(CASE
                WHEN movimento = 'novo' AND situacao_atual = {ativa} THEN estabelecimentos
                WHEN movimento = 'removido' AND situacao_anterior = {ativa} THEN -estabelecimentos
                WHEN movimento = 'mudou_situacao' AND situacao_atual = {ativa} THEN estabelecimentos
                WHEN movimento = 'mudou_situacao' AND situacao_anterior = {ativa} THEN -estabelecimentos
                ELSE 0 END) AS saldo
            FROM {src} WHERE uf IS NOT NULL GROUP BY uf ORDER BY uf
            """,
        )
    ]
    return resumo, transicoes, defasagem, saldo_uf, omitted


def build_document(gold_dir: Path, months: list[str], *, min_cell: int = MIN_CELL) -> dict[str, Any]:
    """Monta o documento em memoria. Nada e gravado aqui."""
    if min_cell < 1:
        raise ExportError("min_cell deve ser pelo menos 1")
    ordered = validate_months(months)
    for month in ordered:
        check_gold(read_manifest(gold_dir, month), month)

    con = duckdb.connect()
    try:
        serie: list[dict] = []
        serie_uf: list[dict] = []
        for month in ordered:
            stock, by_uf = month_stock(con, gold_dir, month)
            serie.append(stock)
            serie_uf.extend(by_uf)
        movimentos: list[dict] = []
        transicoes: list[dict] = []
        defasagem: list[dict] = []
        saldo_uf: list[dict] = []
        omitted = 0
        for previous, month in zip(ordered, ordered[1:], strict=False):
            if not gold_path(gold_dir, MOVEMENTS, month).exists():
                raise ExportError(
                    f"faltam os movimentos de {month}; rode antes: "
                    f"python -m cnpj_pipeline.evolution --months {' '.join(ordered)}"
                )
            resumo, tr, lag, saldo, skipped = month_movements(con, gold_dir, month, previous, min_cell)
            movimentos.append(resumo)
            transicoes += tr
            defasagem += lag
            saldo_uf += saldo
            omitted += skipped
    finally:
        con.close()

    stock_by_month = {s["mes"]: s["estabelecimentos_ativos"] for s in serie}
    for m in movimentos:
        difference = stock_by_month[m["mes"]] - stock_by_month[m["mes_anterior"]]
        if difference != m["saldo_ativos"]:
            raise ExportError(
                f"o saldo de ativos dos movimentos de {m['mes']} ({m['saldo_ativos']}) nao fecha com a "
                f"diferenca entre os estoques ({difference})"
            )

    doc: dict[str, Any] = {
        "arquivo": "evolucao",
        "esquema": SCHEMA_VERSION,
        "titulo": TITULO,
        "descricao": DESCRICAO,
        "ressalvas": dict(RESSALVAS_EVOLUCAO),
        "meses": ordered,
        "celulas_suprimidas": omitted,
        "dados": {
            "serie": serie,
            "serie_uf": serie_uf,
            "movimentos": movimentos,
            "transicoes": transicoes,
            "defasagem": defasagem,
            "saldo_uf": saldo_uf,
        },
    }
    extra = collect_keys(doc) - ALLOWED_KEYS - EVOLUTION_KEYS
    if extra:
        raise ExportError(f"chaves fora da lista permitida: {sorted(extra)}")
    return doc


def run(gold_dir: Path, months: list[str], out_dir: Path, *, min_cell: int = MIN_CELL) -> Path:
    text = dumps(build_document(gold_dir, months, min_cell=min_cell))
    size = len(text.encode("utf-8"))
    if size > MAX_FILE_BYTES:
        raise ExportError(f"{FILENAME} tem {size} bytes, acima do limite de {MAX_FILE_BYTES}")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / FILENAME
    path.write_text(text, encoding="utf-8", newline="\n")
    log.info("exportado %s (%d bytes)", path, size)
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exporta a evolucao entre meses para o site (JSON agregado)."
    )
    parser.add_argument("--months", nargs="+", required=True, metavar="AAAA-MM", help="Meses da serie")
    parser.add_argument("--gold-dir", type=Path, default=config.GOLD_DIR)
    parser.add_argument("--out", type=Path, default=Path("site/public/data"))
    parser.add_argument(
        "--min-cell", type=int, default=MIN_CELL, help="Contagem minima de um recorte publicado"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        path = run(args.gold_dir, args.months, args.out, min_cell=args.min_cell)
    except ExportError as exc:
        print(f"Exportacao recusada: {exc}", file=sys.stderr)
        return 1
    print(f"Arquivo gravado em {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
