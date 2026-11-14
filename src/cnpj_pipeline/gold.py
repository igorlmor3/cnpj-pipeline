"""Camada gold: modelos analiticos (dimensoes, fato e marts) construidos a partir da silver.

Le data/silver/<tabela>/reference_month=<mes>/<tabela>.parquet e grava
data/gold/<modelo>/reference_month=<mes>/<modelo>.parquet. Os modelos estao em gold_models.py:

- dimensoes: CNAE, secao da CNAE, natureza juridica, municipio, qualificacao de socio,
  situacao cadastral e porte;
- fato_estabelecimento: uma linha por CNPJ de 14 posicoes, sem dados de contato nem nomes;
- marts agregados: aberturas por mes, UF e secao; estabelecimentos ativos por municipio e CNAE;
  perfil por porte e natureza juridica; Simples e MEI por UF; socios por qualificacao.

Depois de construir, a gold roda verificacoes de qualidade: as bloqueantes (chave unica do fato,
contagem igual a da silver, soma dos marts igual a do fato, dimensoes sem codigo repetido) fazem o
comando falhar; as informativas (chaves sem correspondencia nas dimensoes) so sao registradas.
O resultado vai para data/gold/_manifest_<mes>.json e para um relatorio com contagens em docs/gold/.

Exemplos:
    python -m cnpj_pipeline.gold --month 2026-10
    python -m cnpj_pipeline.gold --month 2026-10 --models fato_estabelecimento --force
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
from cnpj_pipeline.gold_models import MODEL_NAMES, MODELS, MODELS_BY_NAME, Model
from cnpj_pipeline.silver import silver_path

log = logging.getLogger("cnpj_pipeline.gold")

STATUS_BUILT = "built"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


@dataclass
class ModelResult:
    model: str
    status: str
    rows: int = 0
    seconds: float = 0.0
    parquet: str = ""
    parquet_bytes: int = 0
    columns: dict[str, str] = field(default_factory=dict)
    reason: str | None = None  # por que foi pulada ou falhou


@dataclass
class CheckResult:
    name: str
    ok: bool
    blocking: bool
    detail: dict[str, int] = field(default_factory=dict)
    description: str = ""


# --------------------------------------------------------------------------- #
# Utilitarios
# --------------------------------------------------------------------------- #


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def gold_path(gold_dir: Path, model: str, month: str) -> Path:
    return gold_dir / model / f"reference_month={month}" / f"{model}.parquet"


def _view(con: duckdb.DuckDBPyConnection, name: str, path: Path) -> None:
    con.execute(
        f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet({_q(path.as_posix())}, "
        "hive_partitioning=false)"
    )


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    value = con.execute(sql).fetchone()[0]
    return int(value) if value is not None else 0


def _count(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    return _scalar(con, f"SELECT count(*) FROM read_parquet({_q(path.as_posix())}, hive_partitioning=false)")


# --------------------------------------------------------------------------- #
# Construcao dos modelos
# --------------------------------------------------------------------------- #


def _missing_inputs(
    model: Model, month: str, silver_dir: Path, gold_dir: Path, failed: set[str]
) -> str | None:
    """Motivo pelo qual o modelo nao pode ser construido agora, ou None se tudo esta disponivel."""
    for table in model.sources:
        if not silver_path(silver_dir, table, month).exists():
            return f"falta a silver de {table} para {month}"
    for dep in model.depends:
        if dep in failed:
            return f"o modelo {dep} falhou"
        if not gold_path(gold_dir, dep, month).exists():
            return f"falta o modelo {dep} na gold para {month}"
    return None


def build_model(
    con: duckdb.DuckDBPyConnection,
    model: Model,
    month: str,
    silver_dir: Path,
    gold_dir: Path,
    processed_at: str,
) -> ModelResult:
    out = gold_path(gold_dir, model.name, month)
    result = ModelResult(model=model.name, status=STATUS_FAILED, parquet=out.relative_to(gold_dir).as_posix())

    for table in model.sources:
        _view(con, f"silver_{table.lower()}", silver_path(silver_dir, table, month))
    for dep in model.depends:
        _view(con, f"gold_{dep}", gold_path(gold_dir, dep, month))

    started = time.perf_counter()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.unlink(missing_ok=True)
    con.execute(
        f"COPY ({model.sql(month, processed_at)}) TO {_q(tmp.as_posix())} (FORMAT parquet, COMPRESSION zstd)"
    )
    tmp.replace(out)

    result.rows = _count(con, out)
    result.parquet_bytes = out.stat().st_size
    described = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet({_q(out.as_posix())}, hive_partitioning=false)"
    ).fetchall()
    result.columns = {row[0]: row[1] for row in described}
    result.seconds = round(time.perf_counter() - started, 2)
    result.status = STATUS_BUILT
    log.info("%s: %d linha(s) em %.1fs", model.name, result.rows, result.seconds)
    return result


# --------------------------------------------------------------------------- #
# Verificacoes de qualidade
# --------------------------------------------------------------------------- #

FATO = "gold_fato_estabelecimento"
ATIVAS = "is_matriz AND is_ativo"

# (nome, descricao, modelos da gold necessarios, tabelas da silver necessarias, consulta esquerda,
# consulta direita). A verificacao passa quando as duas consultas retornam o mesmo numero.
RECONCILIATIONS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...], str, str], ...] = (
    (
        "fato_igual_silver",
        "O fato tem uma linha por estabelecimento da silver",
        ("fato_estabelecimento",),
        ("Estabelecimentos",),
        f"SELECT count(*) FROM {FATO}",
        "SELECT count(*) FROM silver_estabelecimentos",
    ),
    (
        "fato_chave_unica",
        "O CNPJ de 14 posicoes nao se repete no fato",
        ("fato_estabelecimento",),
        (),
        f"SELECT count(*) FROM {FATO}",
        f"SELECT count(DISTINCT cnpj) FROM {FATO}",
    ),
    (
        "mart_aberturas_soma",
        "A soma de mart_aberturas_mensal bate com os estabelecimentos do fato que tem data de inicio",
        ("fato_estabelecimento", "mart_aberturas_mensal"),
        (),
        "SELECT sum(estabelecimentos_abertos) FROM gold_mart_aberturas_mensal",
        f"SELECT count(*) FROM {FATO} WHERE data_inicio_atividade IS NOT NULL",
    ),
    (
        "mart_ativos_soma",
        "A soma de mart_estabelecimentos_ativos bate com os estabelecimentos ativos do fato",
        ("fato_estabelecimento", "mart_estabelecimentos_ativos"),
        (),
        "SELECT sum(estabelecimentos_ativos) FROM gold_mart_estabelecimentos_ativos",
        f"SELECT count(*) FROM {FATO} WHERE is_ativo",
    ),
    (
        "mart_perfil_soma",
        "A soma de mart_perfil_porte_natureza bate com as empresas ativas do fato",
        ("fato_estabelecimento", "mart_perfil_porte_natureza"),
        (),
        "SELECT sum(empresas_ativas) FROM gold_mart_perfil_porte_natureza",
        f"SELECT count(*) FROM {FATO} WHERE {ATIVAS}",
    ),
    (
        "mart_simples_empresas_soma",
        "A soma de mart_simples_mei_uf bate com as empresas ativas do fato",
        ("fato_estabelecimento", "mart_simples_mei_uf"),
        (),
        "SELECT sum(empresas_ativas) FROM gold_mart_simples_mei_uf",
        f"SELECT count(*) FROM {FATO} WHERE {ATIVAS}",
    ),
    (
        "mart_simples_optantes_soma",
        "Os optantes do Simples em mart_simples_mei_uf batem com o fato",
        ("fato_estabelecimento", "mart_simples_mei_uf"),
        (),
        "SELECT sum(optantes_simples) FROM gold_mart_simples_mei_uf",
        f"SELECT count(*) FROM {FATO} WHERE {ATIVAS} AND is_optante_simples",
    ),
    (
        "mart_socios_soma",
        "A soma de mart_socios_qualificacao bate com as linhas de Socios na silver",
        ("mart_socios_qualificacao",),
        ("Socios",),
        "SELECT sum(socios) FROM gold_mart_socios_qualificacao",
        "SELECT count(*) FROM silver_socios",
    ),
)

DIMENSIONS_WITH_CODE = (
    "dim_cnae",
    "dim_natureza_juridica",
    "dim_municipio",
    "dim_qualificacao_socio",
    "dim_situacao_cadastral",
    "dim_porte",
    "dim_cnae_secao",
)

ORPHAN_QUERY = f"""
    SELECT
        count(*) AS estabelecimentos,
        count(*) FILTER (WHERE f.uf IS NULL) AS uf_nula,
        count(*) FILTER (WHERE f.data_inicio_atividade IS NULL) AS data_inicio_nula,
        count(*) FILTER (WHERE f.natureza_juridica IS NULL) AS natureza_nula_ou_sem_empresa,
        count(*) FILTER (WHERE f.cnae_principal IS NOT NULL AND c.codigo IS NULL) AS cnae_sem_dimensao,
        count(*) FILTER (WHERE f.municipio IS NOT NULL AND m.codigo IS NULL) AS municipio_sem_dimensao,
        count(*) FILTER (WHERE f.natureza_juridica IS NOT NULL AND n.codigo IS NULL)
            AS natureza_sem_dimensao
    FROM {FATO} f
    LEFT JOIN gold_dim_cnae c ON c.codigo = f.cnae_principal
    LEFT JOIN gold_dim_municipio m ON m.codigo = f.municipio
    LEFT JOIN gold_dim_natureza_juridica n ON n.codigo = f.natureza_juridica
"""


def run_checks(
    con: duckdb.DuckDBPyConnection, month: str, silver_dir: Path, gold_dir: Path
) -> list[CheckResult]:
    """Roda as verificacoes que tem todas as entradas disponiveis; as demais sao omitidas."""
    available = {n for n in MODEL_NAMES if gold_path(gold_dir, n, month).exists()}
    for name in available:
        _view(con, f"gold_{name}", gold_path(gold_dir, name, month))
    silver_ok = set()
    for table in config.ALL_TABLES:
        path = silver_path(silver_dir, table, month)
        if path.exists():
            _view(con, f"silver_{table.lower()}", path)
            silver_ok.add(table)

    results: list[CheckResult] = []
    for name, description, gold_needs, silver_needs, left_sql, right_sql in RECONCILIATIONS:
        if not set(gold_needs) <= available or not set(silver_needs) <= silver_ok:
            continue
        left, right = _scalar(con, left_sql), _scalar(con, right_sql)
        results.append(
            CheckResult(name, left == right, True, {"esquerda": left, "direita": right}, description)
        )

    for dim in DIMENSIONS_WITH_CODE:
        if dim not in available:
            continue
        rows = _scalar(con, f"SELECT count(*) FROM gold_{dim}")
        distinct = _scalar(con, f"SELECT count(DISTINCT codigo) FROM gold_{dim}")
        results.append(
            CheckResult(
                f"{dim}_codigo_unico",
                rows == distinct,
                True,
                {"linhas": rows, "codigos_distintos": distinct},
                f"{dim} nao repete codigo",
            )
        )

    needs = {"fato_estabelecimento", "dim_cnae", "dim_municipio", "dim_natureza_juridica"}
    if needs <= available:
        cur = con.execute(ORPHAN_QUERY)
        names = [d[0] for d in cur.description]
        values = cur.fetchone()
        results.append(
            CheckResult(
                "fato_chaves_sem_correspondencia",
                True,
                False,
                {n: int(v) for n, v in zip(names, values, strict=True)},
                "Informativo: nulos e chaves do fato sem correspondencia nas dimensoes",
            )
        )
    return results


# --------------------------------------------------------------------------- #
# Manifesto, relatorio e orquestracao
# --------------------------------------------------------------------------- #


def write_manifest(gold_dir: Path, month: str, results: list[ModelResult], checks: list[CheckResult]) -> Path:
    path = gold_dir / f"_manifest_{month}.json"
    models: dict[str, dict] = {}
    old_checks: list[dict] = []
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        models = previous.get("models", {})
        old_checks = previous.get("checks", [])
    for r in results:
        if r.status == STATUS_BUILT:
            entry = asdict(r)
            entry.pop("reason", None)
            models[r.model] = entry
    by_name = {c["name"]: c for c in old_checks}
    by_name.update({c.name: asdict(c) for c in checks})
    manifest = {
        "reference_month": month,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "models": dict(sorted(models.items())),
        "checks": sorted(by_name.values(), key=lambda c: c["name"]),
    }
    gold_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def render_report(month: str, manifest: dict) -> str:
    lines = [
        f"# Camada gold: modelos e verificações ({month})",
        "",
        "Este relatório contém apenas contagens.",
        "",
        "## Modelos",
        "",
        "| Modelo | Linhas | Colunas | Tamanho (bytes) |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, m in manifest["models"].items():
        lines.append(f"| `{name}` | {m['rows']} | {len(m['columns'])} | {m['parquet_bytes']} |")
    lines += ["", "## Verificações bloqueantes", "", "| Verificação | Resultado | Esquerda | Direita |"]
    lines.append("| --- | --- | ---: | ---: |")
    for c in manifest["checks"]:
        if not c["blocking"]:
            continue
        d = c["detail"]
        left, right = (d.get("esquerda"), d.get("direita")) if "esquerda" in d else tuple(d.values())
        lines.append(f"| `{c['name']}` | {'ok' if c['ok'] else 'FALHOU'} | {left} | {right} |")
    for c in manifest["checks"]:
        if c["blocking"]:
            continue
        lines += ["", f"## {c['description']}", "", "| Medida | Quantidade |", "| --- | ---: |"]
        lines += [f"| `{k}` | {v} |" for k, v in c["detail"].items()]
    lines.append("")
    return "\n".join(lines)


def run(
    month: str,
    models: list[str] | None,
    *,
    silver_dir: Path,
    gold_dir: Path,
    report_dir: Path | None,
    force: bool,
    memory_limit: str,
    threads: int | None,
    tmp_root: Path,
) -> int:
    if not any(silver_path(silver_dir, t, month).exists() for t in config.ALL_TABLES):
        log.error("Nada para processar. Rode antes: python -m cnpj_pipeline.silver --month %s", month)
        return 1

    selected = [m for m in MODELS if models is None or m.name in models]
    tmp_root.mkdir(parents=True, exist_ok=True)
    options: dict[str, str | int] = {
        "memory_limit": memory_limit,
        "temp_directory": str(tmp_root / "duckdb_spill"),
    }
    if threads:
        options["threads"] = threads
    con = duckdb.connect(config=options)

    processed_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    results: list[ModelResult] = []
    rebuilt: set[str] = set()
    failed: set[str] = set()
    checks: list[CheckResult] = []
    try:
        for model in selected:
            out = gold_path(gold_dir, model.name, month)
            reason = _missing_inputs(model, month, silver_dir, gold_dir, failed)
            if reason:
                log.warning("%s: %s, pulando", model.name, reason)
                results.append(ModelResult(model.name, STATUS_SKIPPED, reason=reason))
                continue
            stale = any(dep in rebuilt for dep in model.depends)
            if out.exists() and not force and not stale:
                log.info("%s: ja existe, pulando (use --force para refazer)", model.name)
                results.append(
                    ModelResult(model.name, STATUS_SKIPPED, rows=_count(con, out), reason="ja existe")
                )
                continue
            try:
                result = build_model(con, model, month, silver_dir, gold_dir, processed_at)
            except duckdb.Error as exc:
                log.error("%s: erro do DuckDB: %s", model.name, exc)
                (out.with_name(out.name + ".tmp")).unlink(missing_ok=True)
                results.append(ModelResult(model.name, STATUS_FAILED, reason=str(exc)))
                failed.add(model.name)
                continue
            results.append(result)
            rebuilt.add(model.name)

        if not failed:
            try:
                checks = run_checks(con, month, silver_dir, gold_dir)
            except duckdb.Error as exc:
                log.error("verificacoes: erro do DuckDB: %s", exc)
                failed.add("verificacoes")
    finally:
        con.close()
        shutil.rmtree(tmp_root / "duckdb_spill", ignore_errors=True)

    manifest_path = write_manifest(gold_dir, month, results, checks)
    if report_dir is not None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{month}.md").write_text(render_report(month, manifest), encoding="utf-8")

    failed_checks = [c for c in checks if c.blocking and not c.ok]
    for c in checks:
        level = logging.INFO if c.ok else logging.ERROR
        log.log(level, "verificacao %s: %s %s", c.name, "ok" if c.ok else "FALHOU", c.detail)
    log.info(
        "Resumo: %d construido(s), %d pulado(s), %d com falha, %d verificacao(oes) bloqueante(s) com falha."
        " Manifesto: %s",
        sum(r.status == STATUS_BUILT for r in results),
        sum(r.status == STATUS_SKIPPED for r in results),
        sum(r.status == STATUS_FAILED for r in results),
        len(failed_checks),
        manifest_path,
    )
    for r in results:
        if r.status == STATUS_FAILED:
            log.error("%s: %s", r.model, r.reason)
    if failed_checks:
        log.error("Nao publique esta gold: corrija a causa e rode de novo com --force.")
    return 1 if failed or failed_checks else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.gold",
        description="Constroi a camada gold (dimensoes, fato e marts) a partir da silver.",
    )
    parser.add_argument("--month", required=True, help="Mes de referencia no formato AAAA-MM")
    parser.add_argument(
        "--models",
        nargs="+",
        metavar="MODELO",
        help=f"Modelos (padrao: todos). Validos: {', '.join(MODEL_NAMES)}",
    )
    parser.add_argument("--silver-dir", type=Path, default=config.SILVER_DIR)
    parser.add_argument("--gold-dir", type=Path, default=config.GOLD_DIR)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=config.PROJECT_ROOT / "docs" / "gold",
        help="Pasta do relatorio de contagens e verificacoes (padrao: docs/gold)",
    )
    parser.add_argument("--force", action="store_true", help="Refaz modelos que ja existem na gold")
    parser.add_argument(
        "--memory-limit", default="4GB", help="Limite de memoria do DuckDB (padrao: %(default)s)"
    )
    parser.add_argument("--threads", type=int, default=None, help="Threads do DuckDB (padrao: automatico)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Logs detalhados")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not MONTH_RE.match(args.month):
        print(f"--month invalido: {args.month!r}. Use AAAA-MM, por exemplo 2026-10.", file=sys.stderr)
        return 2
    if args.models:
        unknown = [m for m in args.models if m not in MODELS_BY_NAME]
        if unknown:
            print(
                f"Modelo(s) desconhecido(s): {', '.join(unknown)}. Validos: {', '.join(MODEL_NAMES)}",
                file=sys.stderr,
            )
            return 2

    return run(
        args.month,
        args.models,
        silver_dir=args.silver_dir,
        gold_dir=args.gold_dir,
        report_dir=args.report_dir,
        force=args.force,
        memory_limit=args.memory_limit,
        threads=args.threads,
        tmp_root=config.DATA_DIR / "tmp",
    )


if __name__ == "__main__":
    sys.exit(main())
