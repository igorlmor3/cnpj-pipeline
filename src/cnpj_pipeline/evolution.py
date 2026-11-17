"""Evolucao entre meses: o que mudou no cadastro de uma foto mensal para a seguinte.

Cada mes da Receita e uma foto completa do cadastro (inclusive estabelecimentos baixados). Comparar
duas fotos consecutivas, CNPJ a CNPJ, mostra o que uma foto sozinha nao mostra:

- novos: CNPJs que aparecem na foto atual e nao estavam na anterior (com a defasagem entre a data de
  inicio da atividade e o mes da foto, que revela cadastros registrados com atraso);
- removidos: CNPJs que estavam na foto anterior e sumiram;
- mudaram de situacao: mesmo CNPJ, situacao cadastral diferente (ativa, suspensa, inapta, baixada...).

O resultado e um mart agregado, `mart_movimentos`, gravado na gold do mes atual em
data/gold/mart_movimentos/reference_month=<mes>/mart_movimentos.parquet. O mart nao tem CNPJ nem
nenhum dado de pessoa: so contagens por tipo de movimento, situacoes, UF e secao da CNAE. Por isso o
site pode ler esse mart, mesmo nao lendo nunca a fato.

Duas verificacoes bloqueantes conferem a aritmetica entre as fotos (total de estabelecimentos e total de
ativos), calculadas a partir das duas fatos de forma independente do mart.

Exemplos:
    python -m cnpj_pipeline.evolution --months 2026-07 2026-08 2026-09 2026-10
    python -m cnpj_pipeline.evolution --months 2026-09 2026-10 --force
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from cnpj_pipeline import config
from cnpj_pipeline.download import MONTH_RE
from cnpj_pipeline.gold import gold_path
from cnpj_pipeline.gold_models import SITUACAO_ATIVA, SITUACOES_CADASTRAIS, meta_sql

log = logging.getLogger("cnpj_pipeline.evolution")

MODEL = "mart_movimentos"
FATO = "fato_estabelecimento"

NOVO = "novo"
REMOVIDO = "removido"
MUDOU = "mudou_situacao"

# Faixas da defasagem entre o mes da foto e o mes do inicio da atividade de um CNPJ novo.
LAG_ORDER = ("ate_1_mes", "2_a_3_meses", "4_a_12_meses", "mais_de_12_meses", "data_futura", "sem_data")
LAG_LABELS = {
    "ate_1_mes": "até 1 mês",
    "2_a_3_meses": "2 a 3 meses",
    "4_a_12_meses": "4 a 12 meses",
    "mais_de_12_meses": "mais de 12 meses",
    "data_futura": "data de início posterior à foto",
    "sem_data": "sem data de início",
}

FATO_COLUMNS = (
    "cnpj",
    "is_matriz",
    "situacao_cadastral",
    "uf",
    "cnae_secao",
    "is_organizacao_politica",
    "data_inicio_atividade",
)


class EvolutionError(RuntimeError):
    """Entrada ausente ou reprovada: a evolucao nao pode ser calculada."""


@dataclass
class CheckResult:
    name: str
    ok: bool
    blocking: bool
    detail: dict[str, int] = field(default_factory=dict)
    description: str = ""


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    value = con.execute(sql).fetchone()[0]
    return int(value) if value is not None else 0


def _read(path: Path) -> str:
    return f"read_parquet({_q(path.as_posix())}, hive_partitioning=false)"


def previous_pairs(months: list[str]) -> list[tuple[str, str]]:
    """Pares (anterior, atual) de meses consecutivos na lista, ja ordenada e sem repeticao."""
    if len(months) < 2:
        raise EvolutionError("informe pelo menos dois meses")
    if len(set(months)) != len(months):
        raise EvolutionError("mes repetido na lista")
    for month in months:
        if not MONTH_RE.match(month):
            raise EvolutionError(f"mes invalido: {month!r} (formato AAAA-MM)")
    ordered = sorted(months)
    return list(zip(ordered, ordered[1:], strict=False))


def manifest_path(gold_dir: Path, month: str) -> Path:
    return gold_dir / f"_evolution_{month}.json"


def require_gold(gold_dir: Path, month: str) -> Path:
    """A fato do mes precisa existir e a gold do mes ter passado em todas as verificacoes bloqueantes."""
    fato = gold_path(gold_dir, FATO, month)
    if not fato.exists():
        raise EvolutionError(f"fato da gold ausente para {month}: {fato}")
    manifest_file = gold_dir / f"_manifest_{month}.json"
    if not manifest_file.exists():
        raise EvolutionError(f"manifesto da gold ausente para {month}; rode a gold antes")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    checks = manifest.get("checks", [])
    failed = [c["name"] for c in checks if c.get("blocking") and not c.get("ok")]
    if manifest.get("reference_month") != month or not checks or failed:
        raise EvolutionError(
            f"a gold de {month} nao esta aprovada (verificacoes: {', '.join(failed) or 'ausentes'})"
        )
    return fato


# --------------------------------------------------------------------------- #
# O mart
# --------------------------------------------------------------------------- #


def lag_case(column: str, month: str) -> str:
    """Faixa da defasagem, em meses, entre o inicio da atividade e o mes da foto."""
    lag = f"date_diff('month', date_trunc('month', {column}), DATE {_q(month + '-01')})"
    return (
        f"CASE WHEN {column} IS NULL THEN 'sem_data' WHEN {lag} < 0 THEN 'data_futura' "
        f"WHEN {lag} <= 1 THEN 'ate_1_mes' WHEN {lag} <= 3 THEN '2_a_3_meses' "
        f"WHEN {lag} <= 12 THEN '4_a_12_meses' ELSE 'mais_de_12_meses' END"
    )


def movimentos_sql(current: Path, previous_path: Path, month: str, previous: str, processed_at: str) -> str:
    cols = ", ".join(FATO_COLUMNS)
    return f"""
        WITH c AS (SELECT {cols} FROM {_read(current)}),
             p AS (SELECT {cols} FROM {_read(previous_path)})
        SELECT
            CAST({_q(previous)} AS VARCHAR) AS mes_anterior,
            CASE WHEN p.cnpj IS NULL THEN '{NOVO}' WHEN c.cnpj IS NULL THEN '{REMOVIDO}'
                 ELSE '{MUDOU}' END AS movimento,
            p.situacao_cadastral AS situacao_anterior,
            c.situacao_cadastral AS situacao_atual,
            CASE WHEN c.cnpj IS NULL THEN p.uf ELSE c.uf END AS uf,
            CASE WHEN c.cnpj IS NULL THEN p.cnae_secao ELSE c.cnae_secao END AS cnae_secao,
            CASE WHEN c.cnpj IS NULL THEN p.is_organizacao_politica
                 ELSE c.is_organizacao_politica END AS is_organizacao_politica,
            CASE WHEN p.cnpj IS NULL THEN {lag_case("c.data_inicio_atividade", month)}
            END AS defasagem_inicio,
            count(*) AS estabelecimentos,
            count(*) FILTER (
                WHERE CASE WHEN c.cnpj IS NULL THEN p.is_matriz ELSE c.is_matriz END
            ) AS matrizes,
            {meta_sql(month, processed_at)}
        FROM c FULL OUTER JOIN p ON c.cnpj = p.cnpj
        WHERE p.cnpj IS NULL OR c.cnpj IS NULL OR c.situacao_cadastral IS DISTINCT FROM p.situacao_cadastral
        GROUP BY ALL
        ORDER BY movimento, situacao_anterior, situacao_atual, uf, cnae_secao, is_organizacao_politica,
            defasagem_inicio
    """


def run_checks(
    con: duckdb.DuckDBPyConnection, current: Path, previous_path: Path, mart: Path
) -> list[CheckResult]:
    """Aritmetica entre as fotos: o que a fato atual tem a mais ou a menos que a anterior tem de ser o
    que o mart registrou como novo, removido e mudanca de situacao."""
    m = _read(mart)
    ativa = _q(SITUACAO_ATIVA)

    def movement(where: str) -> int:
        return _scalar(con, f"SELECT coalesce(sum(estabelecimentos), 0) FROM {m} WHERE {where}")

    novos = movement(f"movimento = '{NOVO}'")
    removidos = movement(f"movimento = '{REMOVIDO}'")
    novos_ativos = movement(f"movimento = '{NOVO}' AND situacao_atual = {ativa}")
    removidos_ativos = movement(f"movimento = '{REMOVIDO}' AND situacao_anterior = {ativa}")
    passaram_a_ativos = movement(f"movimento = '{MUDOU}' AND situacao_atual = {ativa}")
    deixaram_de_ser_ativos = movement(f"movimento = '{MUDOU}' AND situacao_anterior = {ativa}")

    total_atual = _scalar(con, f"SELECT count(*) FROM {_read(current)}")
    total_anterior = _scalar(con, f"SELECT count(*) FROM {_read(previous_path)}")
    ativos_atual = _scalar(con, f"SELECT count(*) FROM {_read(current)} WHERE situacao_cadastral = {ativa}")
    ativos_anterior = _scalar(
        con, f"SELECT count(*) FROM {_read(previous_path)} WHERE situacao_cadastral = {ativa}"
    )

    esperado_total = total_anterior + novos - removidos
    esperado_ativos = (
        ativos_anterior + novos_ativos - removidos_ativos + passaram_a_ativos - deixaram_de_ser_ativos
    )
    return [
        CheckResult(
            "movimentos_fecham_o_total",
            total_atual == esperado_total,
            True,
            {"atual": total_atual, "anterior_mais_novos_menos_removidos": esperado_total},
            "Estabelecimentos da foto atual = anterior + novos - removidos",
        ),
        CheckResult(
            "movimentos_fecham_os_ativos",
            ativos_atual == esperado_ativos,
            True,
            {"atual": ativos_atual, "esperado_pelos_movimentos": esperado_ativos},
            "Ativos da foto atual = anterior + novos ativos - removidos ativos + passaram a ativos "
            "- deixaram de ser ativos",
        ),
    ]


# --------------------------------------------------------------------------- #
# Relatorio
# --------------------------------------------------------------------------- #


def _situacao_label(code: str | None) -> str:
    if code is None:
        return "(não existia)"
    return dict(SITUACOES_CADASTRAIS).get(code, f"código {code}")


def _fmt(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def summarize(con: duckdb.DuckDBPyConnection, mart: Path) -> dict:
    """Contagens do mart para o manifesto e o relatorio (nenhuma linha individual)."""
    m = _read(mart)
    ativa = _q(SITUACAO_ATIVA)

    def rows(sql: str) -> list[tuple]:
        return con.execute(sql).fetchall()

    def total(where: str) -> int:
        return _scalar(con, f"SELECT coalesce(sum(estabelecimentos), 0) FROM {m} WHERE {where}")

    return {
        "novos": total(f"movimento = '{NOVO}'"),
        "novos_ativos": total(f"movimento = '{NOVO}' AND situacao_atual = {ativa}"),
        "removidos": total(f"movimento = '{REMOVIDO}'"),
        "removidos_ativos": total(f"movimento = '{REMOVIDO}' AND situacao_anterior = {ativa}"),
        "mudaram_de_situacao": total(f"movimento = '{MUDOU}'"),
        "passaram_a_ativos": total(f"movimento = '{MUDOU}' AND situacao_atual = {ativa}"),
        "deixaram_de_ser_ativos": total(f"movimento = '{MUDOU}' AND situacao_anterior = {ativa}"),
        "transicoes": [
            {"de": a, "para": b, "estabelecimentos": int(n)}
            for a, b, n in rows(
                f"SELECT situacao_anterior, situacao_atual, sum(estabelecimentos) n FROM {m} "
                f"WHERE movimento = '{MUDOU}' GROUP BY 1, 2 ORDER BY n DESC, 1, 2"
            )
        ],
        "novos_por_defasagem": {
            lag: int(n)
            for lag, n in rows(
                f"SELECT defasagem_inicio, sum(estabelecimentos) FROM {m} "
                f"WHERE movimento = '{NOVO}' GROUP BY 1"
            )
        },
        "saldo_ativos_por_uf": [
            {"uf": uf, "saldo": int(n)}
            for uf, n in rows(
                f"SELECT uf, sum(CASE "
                f"WHEN movimento = '{NOVO}' AND situacao_atual = {ativa} THEN estabelecimentos "
                f"WHEN movimento = '{REMOVIDO}' AND situacao_anterior = {ativa} THEN -estabelecimentos "
                f"WHEN movimento = '{MUDOU}' AND situacao_atual = {ativa} THEN estabelecimentos "
                f"WHEN movimento = '{MUDOU}' AND situacao_anterior = {ativa} THEN -estabelecimentos "
                f"ELSE 0 END) AS n FROM {m} WHERE uf IS NOT NULL GROUP BY uf ORDER BY n DESC, uf"
            )
        ],
    }


def render_report(month: str, previous: str, manifest: dict) -> str:
    s = manifest["summary"]
    saldo = s["novos_ativos"] - s["removidos_ativos"] + s["passaram_a_ativos"] - s["deixaram_de_ser_ativos"]
    lines = [
        f"# Evolução do cadastro: de {previous} para {month}",
        "",
        "Este relatório contém apenas contagens. Compara duas fotos mensais completas do cadastro, "
        "CNPJ a CNPJ, e não publica nenhum CNPJ.",
        "",
        "## Movimentos entre as duas fotos",
        "",
        "| Movimento | Estabelecimentos | Dos quais ativos |",
        "| --- | ---: | ---: |",
        f"| Novos na foto atual | {_fmt(s['novos'])} | {_fmt(s['novos_ativos'])} |",
        f"| Que sumiram da foto anterior | {_fmt(s['removidos'])} | {_fmt(s['removidos_ativos'])} |",
        f"| Mudaram de situação cadastral | {_fmt(s['mudaram_de_situacao'])} | "
        f"{_fmt(s['passaram_a_ativos'])} passaram a ativos; "
        f"{_fmt(s['deixaram_de_ser_ativos'])} deixaram de ser |",
        "",
        f"Variação líquida de estabelecimentos ativos: {'+' if saldo >= 0 else '-'}{_fmt(abs(saldo))}.",
        "",
        "## Mudanças de situação",
        "",
        "| De | Para | Estabelecimentos |",
        "| --- | --- | ---: |",
    ]
    lines += [
        f"| {_situacao_label(t['de'])} | {_situacao_label(t['para'])} | {_fmt(t['estabelecimentos'])} |"
        for t in s["transicoes"]
    ]
    lines += [
        "",
        "## Defasagem dos CNPJs novos",
        "",
        "Meses entre o início da atividade registrado e o mês da foto. Valores altos indicam cadastros "
        "registrados com atraso, e não aberturas do mês.",
        "",
        "| Defasagem | Novos | Parte |",
        "| --- | ---: | ---: |",
    ]
    total_new = sum(s["novos_por_defasagem"].values()) or 1
    for lag in LAG_ORDER:
        n = s["novos_por_defasagem"].get(lag, 0)
        if n:
            share = f"{100 * n / total_new:.1f}".replace(".", ",")
            lines.append(f"| {LAG_LABELS[lag]} | {_fmt(n)} | {share}% |")
    lines += ["", "## Variação líquida de ativos por UF", "", "| UF | Variação |", "| --- | ---: |"]
    lines += [
        f"| {r['uf']} | {'+' if r['saldo'] >= 0 else '-'}{_fmt(abs(r['saldo']))} |"
        for r in s["saldo_ativos_por_uf"]
    ]
    lines += [
        "",
        "## Verificações bloqueantes",
        "",
        "| Verificação | Resultado | Esquerda | Direita |",
        "| --- | --- | ---: | ---: |",
    ]
    for c in manifest["checks"]:
        left, right = c["detail"].values()
        lines.append(f"| `{c['name']}` | {'ok' if c['ok'] else 'FALHOU'} | {_fmt(left)} | {_fmt(right)} |")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Execucao
# --------------------------------------------------------------------------- #


def build_month(
    gold_dir: Path,
    month: str,
    previous: str,
    *,
    memory_limit: str,
    threads: int | None,
    tmp_root: Path,
    report_dir: Path | None,
    force: bool,
) -> dict | None:
    """Constroi o mart de um mes. Devolve o manifesto, ou None se ja existia e nao ha --force."""
    current = require_gold(gold_dir, month)
    previous_path = require_gold(gold_dir, previous)
    out = gold_path(gold_dir, MODEL, month)
    if out.exists() and not force:
        log.info("%s %s: ja existe, pulando (use --force para refazer)", MODEL, month)
        return None

    tmp_root.mkdir(parents=True, exist_ok=True)
    options: dict[str, str | int] = {
        "memory_limit": memory_limit,
        "temp_directory": str(tmp_root / "duckdb_spill"),
    }
    if threads:
        options["threads"] = threads
    con = duckdb.connect(config=options)
    started = time.perf_counter()
    processed_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        tmp.unlink(missing_ok=True)
        con.execute(
            f"COPY ({movimentos_sql(current, previous_path, month, previous, processed_at)}) "
            f"TO {_q(tmp.as_posix())} (FORMAT parquet, COMPRESSION zstd)"
        )
        tmp.replace(out)
        checks = run_checks(con, current, previous_path, out)
        summary = summarize(con, out)
        rows = _scalar(con, f"SELECT count(*) FROM {_read(out)}")
    except duckdb.Error as exc:
        out.with_name(out.name + ".tmp").unlink(missing_ok=True)
        raise EvolutionError(f"erro do DuckDB em {month}: {exc}") from exc
    finally:
        con.close()
        shutil.rmtree(tmp_root / "duckdb_spill", ignore_errors=True)

    manifest = {
        "reference_month": month,
        "previous_month": previous,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": rows,
        "parquet_bytes": out.stat().st_size,
        "seconds": round(time.perf_counter() - started, 2),
        "checks": [asdict(c) for c in checks],
        "summary": summary,
    }
    manifest_path(gold_dir, month).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{month}.md").write_text(render_report(month, previous, manifest), encoding="utf-8")
    for c in checks:
        log.log(
            logging.INFO if c.ok else logging.ERROR,
            "verificacao %s: %s %s",
            c.name,
            "ok" if c.ok else "FALHOU",
            c.detail,
        )
    log.info("%s %s: %d linha(s) em %.1fs", MODEL, month, rows, manifest["seconds"])
    if any(c.blocking and not c.ok for c in checks):
        raise EvolutionError(f"verificacoes bloqueantes falharam em {month}; nao publique este mart")
    return manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compara fotos mensais consecutivas da gold e grava o mart de movimentos de cada mes "
        "(exceto o primeiro da lista)."
    )
    parser.add_argument("--months", nargs="+", required=True, metavar="AAAA-MM", help="Meses a comparar")
    parser.add_argument("--gold-dir", type=Path, default=config.GOLD_DIR)
    parser.add_argument("--report-dir", type=Path, default=Path("docs/evolution"))
    parser.add_argument("--no-report", action="store_true", help="Nao grava o relatorio em docs/")
    parser.add_argument("--force", action="store_true", help="Refaz o mart que ja existe")
    parser.add_argument(
        "--memory-limit", default="4GB", help="Limite de memoria do DuckDB (padrao: %(default)s)"
    )
    parser.add_argument("--threads", type=int, default=None, help="Threads do DuckDB (padrao: automatico)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        pairs = previous_pairs(args.months)
        for previous, month in pairs:
            build_month(
                args.gold_dir,
                month,
                previous,
                memory_limit=args.memory_limit,
                threads=args.threads,
                tmp_root=config.DATA_DIR / "tmp",
                report_dir=None if args.no_report else args.report_dir,
                force=args.force,
            )
    except EvolutionError as exc:
        print(f"Evolucao recusada: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
