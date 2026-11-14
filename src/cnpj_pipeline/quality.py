"""Verificacoes de qualidade de dados sobre a silver e a gold de um mes, e tempos medidos.

Le os Parquet da silver (e os manifestos da bronze, da silver e da gold) e confere:

- formato do CNPJ e digitos verificadores (modulo 11, com suporte ao CNPJ alfanumerico);
- intervalos e coerencia de datas (futuras, anteriores a 1900, situacao antes do inicio);
- dominios de codigos (matriz/filial, situacao cadastral, porte, tipo de socio);
- unicidade de chaves, empresas sem matriz e socios sem empresa;
- contagens entre camadas (bronze = entrada da silver; saida + quarentena = entrada) e o resultado
  das verificacoes bloqueantes da gold.

Cada verificacao e bloqueante (falha o comando se passar da tolerancia) ou informativa (so registra a
contagem). O resultado vai para data/quality/_manifest_<mes>.json e para um relatorio SOMENTE com
contagens em docs/quality/<mes>.md, que tambem traz os tempos de cada camada, lidos dos manifestos, e
a especificacao da maquina em que a verificacao rodou.

Exemplos:
    python -m cnpj_pipeline.quality --month 2026-10
    python -m cnpj_pipeline.quality --month 2026-10 --quick
"""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from cnpj_pipeline import config
from cnpj_pipeline.download import MONTH_RE
from cnpj_pipeline.gold_models import MODEL_NAMES
from cnpj_pipeline.silver import quarantine_path, silver_path

log = logging.getLogger("cnpj_pipeline.quality")

STATUS_OK = "ok"
STATUS_WARNING = "aviso"
STATUS_FAILED = "falhou"
STATUS_SKIPPED = "pulada"

# Pesos do modulo 11 do CNPJ: 12 posicoes para o primeiro digito; as mesmas deslocadas, mais o
# primeiro digito, para o segundo. Cada caractere vale ord(c) - 48 (letras A..Z valem 17..42).
_WEIGHTS_1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_WEIGHTS_2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3)


@dataclass
class QualityCheck:
    name: str
    table: str
    description: str
    blocking: bool
    status: str = STATUS_OK
    violations: int = 0
    total: int = 0
    tolerance: int = 0
    reason: str | None = None


@dataclass(frozen=True)
class Rule:
    """Regra por linha: `violation` e uma expressao SQL verdadeira nas linhas que a violam."""

    name: str
    table: str
    description: str
    violation: str
    blocking: bool = False
    tolerance: int = 0


@dataclass(frozen=True)
class SetCheck:
    """Verificacao sobre o conjunto: uma consulta que devolve a quantidade de violacoes."""

    name: str
    table: str
    description: str
    sql: str
    blocking: bool
    heavy: bool = False


# --------------------------------------------------------------------------- #
# Utilitarios
# --------------------------------------------------------------------------- #


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def month_end(month: str) -> date:
    year, mon = int(month[:4]), int(month[5:7])
    return date(year, mon, calendar.monthrange(year, mon)[1])


def _view_name(table: str) -> str:
    return f"silver_{table.lower()}"


def register_macros(con: duckdb.DuckDBPyConnection) -> None:
    """Macros do digito verificador do CNPJ (modulo 11), validas tambem para o CNPJ alfanumerico."""

    def char_sum(weights: tuple[int, ...]) -> str:
        return " + ".join(f"(ascii(substr(k, {i + 1}, 1)) - 48) * {w}" for i, w in enumerate(weights))

    con.execute(f"CREATE OR REPLACE MACRO cnpj_s1(k) AS ({char_sum(_WEIGHTS_1)})")
    con.execute(
        "CREATE OR REPLACE MACRO cnpj_d1(k) AS "
        "(CASE WHEN cnpj_s1(k) % 11 < 2 THEN 0 ELSE 11 - cnpj_s1(k) % 11 END)"
    )
    con.execute(f"CREATE OR REPLACE MACRO cnpj_s2(k) AS ({char_sum(_WEIGHTS_2)} + cnpj_d1(k) * 2)")
    con.execute(
        "CREATE OR REPLACE MACRO cnpj_d2(k) AS "
        "(CASE WHEN cnpj_s2(k) % 11 < 2 THEN 0 ELSE 11 - cnpj_s2(k) % 11 END)"
    )
    con.execute(
        "CREATE OR REPLACE MACRO cnpj_calc_dv(k) AS "
        "(CAST(cnpj_d1(k) AS VARCHAR) || CAST(cnpj_d2(k) AS VARCHAR))"
    )


def machine_info() -> dict:
    """Especificacao da maquina e versoes. Campos que nao puderem ser lidos ficam None."""
    info: dict = {
        "sistema": f"{platform.system()} {platform.release()}",
        "processador": platform.processor() or None,
        "cpus_logicas": os.cpu_count(),
        "memoria_total_mib": _total_memory_mib(),
        "python": platform.python_version(),
        "duckdb": duckdb.__version__,
    }
    return info


def _total_memory_mib() -> int | None:
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [  # noqa: RUF012
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys // (1024 * 1024))
            return None
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size // (1024 * 1024))
    except Exception:  # noqa: BLE001 - informacao opcional; nunca deve derrubar a verificacao
        return None


def peak_memory_mib() -> int | None:
    """Pico de memoria residente deste processo (inclui o DuckDB, que roda no mesmo processo)."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [  # noqa: RUF012
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return int(counters.PeakWorkingSetSize // (1024 * 1024))
            return None
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux informa em KiB; macOS, em bytes.
        return int(peak // 1024) if sys.platform != "darwin" else int(peak // (1024 * 1024))
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Regras por linha
# --------------------------------------------------------------------------- #


def row_rules(month: str) -> list[Rule]:
    end = f"DATE '{month_end(month).isoformat()}'"
    return [
        # --- Estabelecimentos
        Rule(
            "estabelecimento_formato_cnpj",
            "Estabelecimentos",
            "CNPJ de 14 posições: 12 de base e ordem (dígitos ou letras maiúsculas) e 2 dígitos de controle",
            "NOT regexp_matches(cnpj_basico || cnpj_ordem || cnpj_dv, '^[0-9A-Z]{12}[0-9]{2}$')",
            blocking=True,
        ),
        Rule(
            "estabelecimento_digito_verificador",
            "Estabelecimentos",
            "Dígitos verificadores do CNPJ conferem com o cálculo do módulo 11",
            "regexp_matches(cnpj_basico || cnpj_ordem, '^[0-9A-Z]{12}$') "
            "AND cnpj_dv <> cnpj_calc_dv(cnpj_basico || cnpj_ordem)",
        ),
        Rule(
            "estabelecimento_matriz_filial",
            "Estabelecimentos",
            "Identificador matriz/filial é 1 ou 2",
            "identificador_matriz_filial IS NULL OR identificador_matriz_filial NOT IN ('1', '2')",
        ),
        Rule(
            "estabelecimento_matriz_ordem",
            "Estabelecimentos",
            "Matriz tem ordem 0001",
            "identificador_matriz_filial = '1' AND cnpj_ordem <> '0001'",
        ),
        Rule(
            "estabelecimento_situacao",
            "Estabelecimentos",
            "Situação cadastral é 01, 02, 03, 04 ou 08",
            "situacao_cadastral IS NULL OR situacao_cadastral NOT IN ('01', '02', '03', '04', '08')",
        ),
        Rule(
            "estabelecimento_uf_nula",
            "Estabelecimentos",
            "UF válida (a silver anula valores fora das 27 siglas e EX)",
            "uf IS NULL",
        ),
        Rule(
            "estabelecimento_cnae_formato",
            "Estabelecimentos",
            "CNAE principal com 7 dígitos",
            "cnae_fiscal_principal IS NULL OR NOT regexp_matches(cnae_fiscal_principal, '^[0-9]{7}$')",
        ),
        Rule(
            "estabelecimento_inicio_nulo",
            "Estabelecimentos",
            "Data de início de atividade preenchida",
            "data_inicio_atividade IS NULL",
        ),
        Rule(
            "estabelecimento_inicio_futuro",
            "Estabelecimentos",
            "Data de início de atividade não posterior ao fim do mês de referência",
            f"data_inicio_atividade > {end}",
        ),
        Rule(
            "estabelecimento_inicio_antes_1900",
            "Estabelecimentos",
            "Data de início de atividade a partir de 1900",
            "data_inicio_atividade < DATE '1900-01-01'",
        ),
        Rule(
            "estabelecimento_situacao_futura",
            "Estabelecimentos",
            "Data da situação cadastral não posterior ao fim do mês de referência",
            f"data_situacao_cadastral > {end}",
        ),
        Rule(
            "estabelecimento_situacao_antes_inicio",
            "Estabelecimentos",
            "Data da situação cadastral não anterior ao início da atividade",
            "data_situacao_cadastral < data_inicio_atividade",
        ),
        # --- Empresas
        Rule(
            "empresa_porte",
            "Empresas",
            "Porte da empresa preenchido e igual a 00, 01, 03 ou 05",
            "porte_empresa IS NULL OR porte_empresa NOT IN ('00', '01', '03', '05')",
        ),
        Rule(
            "empresa_natureza_formato",
            "Empresas",
            "Natureza jurídica com 4 dígitos",
            "natureza_juridica IS NULL OR NOT regexp_matches(natureza_juridica, '^[0-9]{4}$')",
        ),
        Rule(
            "empresa_capital_negativo",
            "Empresas",
            "Capital social não negativo",
            "capital_social < 0",
        ),
        # --- Simples
        Rule(
            "simples_exclusao_antes_opcao",
            "Simples",
            "Exclusão do Simples não anterior à opção",
            "data_exclusao_simples < data_opcao_simples",
        ),
        Rule(
            "simples_mei_exclusao_antes_opcao",
            "Simples",
            "Exclusão do MEI não anterior à opção",
            "data_exclusao_mei < data_opcao_mei",
        ),
        Rule(
            "simples_opcao_futura",
            "Simples",
            "Datas de opção não posteriores ao fim do mês (opções agendadas para o futuro aparecem aqui)",
            f"data_opcao_simples > {end} OR data_opcao_mei > {end}",
        ),
        # --- Socios
        Rule(
            "socio_identificador",
            "Socios",
            "Identificador de sócio é 1, 2 ou 3",
            "identificador_socio IS NULL OR identificador_socio NOT IN ('1', '2', '3')",
        ),
        Rule(
            "socio_entrada_futura",
            "Socios",
            "Data de entrada na sociedade não posterior ao fim do mês de referência",
            f"data_entrada_sociedade > {end}",
        ),
    ]


# --------------------------------------------------------------------------- #
# Verificacoes sobre o conjunto
# --------------------------------------------------------------------------- #

DOMAIN_TABLES = ("Cnaes", "Motivos", "Municipios", "Naturezas", "Paises", "Qualificacoes")


def set_checks() -> list[SetCheck]:
    checks = [
        SetCheck(
            "estabelecimento_chave_unica",
            "Estabelecimentos",
            "Nenhum CNPJ de 14 posições repetido",
            "SELECT count(*) FROM (SELECT 1 FROM silver_estabelecimentos "
            "GROUP BY cnpj_basico, cnpj_ordem, cnpj_dv HAVING count(*) > 1)",
            blocking=True,
            heavy=True,
        ),
        SetCheck(
            "empresa_chave_unica",
            "Empresas",
            "Nenhum CNPJ base repetido",
            "SELECT count(*) FROM (SELECT 1 FROM silver_empresas GROUP BY cnpj_basico HAVING count(*) > 1)",
            blocking=True,
            heavy=True,
        ),
        SetCheck(
            "simples_chave_unica",
            "Simples",
            "Nenhum CNPJ base repetido",
            "SELECT count(*) FROM (SELECT 1 FROM silver_simples GROUP BY cnpj_basico HAVING count(*) > 1)",
            blocking=True,
        ),
        SetCheck(
            "empresa_sem_matriz",
            "Empresas",
            "Toda empresa tem um estabelecimento matriz",
            "SELECT count(*) FROM silver_empresas e WHERE NOT EXISTS ("
            "SELECT 1 FROM silver_estabelecimentos x "
            "WHERE x.cnpj_basico = e.cnpj_basico AND x.identificador_matriz_filial = '1')",
            blocking=False,
            heavy=True,
        ),
        SetCheck(
            "socio_sem_empresa",
            "Socios",
            "Todo registro de sócio pertence a uma empresa existente",
            "SELECT count(*) FROM silver_socios s WHERE NOT EXISTS ("
            "SELECT 1 FROM silver_empresas e WHERE e.cnpj_basico = s.cnpj_basico)",
            blocking=False,
            heavy=True,
        ),
    ]
    for table in DOMAIN_TABLES:
        checks.append(
            SetCheck(
                f"{table.lower()}_codigo_unico",
                table,
                "Nenhum código repetido",
                f"SELECT count(*) FROM (SELECT 1 FROM {_view_name(table)} "
                "GROUP BY codigo HAVING count(*) > 1)",
                blocking=True,
            )
        )
    return checks


# --------------------------------------------------------------------------- #
# Execucao
# --------------------------------------------------------------------------- #


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    value = con.execute(sql).fetchone()[0]
    return int(value) if value is not None else 0


def _finish(check: QualityCheck) -> QualityCheck:
    if check.status == STATUS_SKIPPED:
        return check
    if check.violations <= check.tolerance:
        check.status = STATUS_OK
    else:
        check.status = STATUS_FAILED if check.blocking else STATUS_WARNING
    return check


def run_row_rules(
    con: duckdb.DuckDBPyConnection, month: str, available: set[str]
) -> tuple[list[QualityCheck], dict[str, float]]:
    results: list[QualityCheck] = []
    seconds: dict[str, float] = {}
    rules = row_rules(month)
    for table in dict.fromkeys(r.table for r in rules):
        table_rules = [r for r in rules if r.table == table]
        if table not in available:
            for r in table_rules:
                results.append(
                    QualityCheck(
                        r.name, table, r.description, r.blocking, STATUS_SKIPPED, reason="sem silver"
                    )
                )
            continue
        started = time.monotonic()
        select = ", ".join(
            f"count(*) FILTER (WHERE ({r.violation})) AS r{i}" for i, r in enumerate(table_rules)
        )
        row = con.execute(f"SELECT count(*) AS total, {select} FROM {_view_name(table)}").fetchone()
        seconds[table] = round(time.monotonic() - started, 2)
        total = int(row[0])
        for i, r in enumerate(table_rules):
            results.append(
                _finish(
                    QualityCheck(
                        r.name,
                        table,
                        r.description,
                        r.blocking,
                        violations=int(row[i + 1]),
                        total=total,
                        tolerance=r.tolerance,
                    )
                )
            )
        log.info("%s: %d regra(s) por linha em %.1f s", table, len(table_rules), seconds[table])
    return results, seconds


def run_set_checks(
    con: duckdb.DuckDBPyConnection, available: set[str], *, quick: bool
) -> tuple[list[QualityCheck], dict[str, float]]:
    results: list[QualityCheck] = []
    seconds: dict[str, float] = {}
    for check in set_checks():
        base = QualityCheck(check.name, check.table, check.description, check.blocking)
        needs = {check.table}
        if check.name == "empresa_sem_matriz":
            needs.add("Estabelecimentos")
        if check.name == "socio_sem_empresa":
            needs.add("Empresas")
        if not needs <= available:
            base.status, base.reason = STATUS_SKIPPED, "sem silver"
            results.append(base)
            continue
        if quick and check.heavy:
            base.status, base.reason = STATUS_SKIPPED, "pulada com --quick"
            results.append(base)
            continue
        started = time.monotonic()
        base.violations = _scalar(con, check.sql)
        base.total = _scalar(con, f"SELECT count(*) FROM {_view_name(check.table)}")
        seconds[check.name] = round(time.monotonic() - started, 2)
        results.append(_finish(base))
        log.info("%s: %d violacao(oes) em %.1f s", check.name, base.violations, seconds[check.name])
    return results, seconds


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _count(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    if not path.exists():
        return 0
    return _scalar(con, f"SELECT count(*) FROM read_parquet({_q(path.as_posix())}, hive_partitioning=false)")


def run_layer_checks(
    con: duckdb.DuckDBPyConnection,
    month: str,
    available: set[str],
    *,
    bronze_manifest: dict | None,
    silver_manifest: dict | None,
    gold_manifest: dict | None,
    silver_dir: Path,
) -> list[QualityCheck]:
    """Contagens entre camadas e resultado das verificacoes bloqueantes da gold."""
    results: list[QualityCheck] = []

    def skipped(name: str, table: str, description: str, reason: str, blocking: bool = True) -> None:
        results.append(QualityCheck(name, table, description, blocking, STATUS_SKIPPED, reason=reason))

    bronze_rows: dict[str, int] = {}
    if bronze_manifest:
        for entry in bronze_manifest.get("files", []):
            bronze_rows[entry["table"]] = bronze_rows.get(entry["table"], 0) + int(entry["rows"])

    for table in config.ALL_TABLES:
        desc_a = "Linhas da bronze = linhas de entrada da silver"
        desc_b = "Linhas de saída + inválidas + duplicadas removidas = linhas de entrada da silver"
        desc_c = "Linhas do Parquet da silver = linhas de saída registradas no manifesto"
        desc_d = "Linhas do Parquet da quarentena = inválidas + duplicadas removidas"
        name = table.lower()
        entry = (silver_manifest or {}).get("tables", {}).get(table)
        if not entry or table not in available:
            for suffix, desc in (
                ("bronze_x_silver", desc_a),
                ("silver_conserva_linhas", desc_b),
                ("silver_parquet_x_manifesto", desc_c),
                ("quarentena_x_manifesto", desc_d),
            ):
                skipped(f"{name}_{suffix}", table, desc, "sem manifesto ou silver da tabela")
            continue
        rows_in, rows_out = int(entry["rows_in"]), int(entry["rows_out"])
        removed = int(entry["invalid_key_rows"]) + int(entry["duplicate_rows_removed"])

        if table in bronze_rows:
            results.append(
                _finish(
                    QualityCheck(
                        f"{name}_bronze_x_silver",
                        table,
                        desc_a,
                        True,
                        violations=abs(bronze_rows[table] - rows_in),
                        total=rows_in,
                    )
                )
            )
        else:
            skipped(f"{name}_bronze_x_silver", table, desc_a, "sem manifesto da bronze")

        results.append(
            _finish(
                QualityCheck(
                    f"{name}_silver_conserva_linhas",
                    table,
                    desc_b,
                    True,
                    violations=abs(rows_in - (rows_out + removed)),
                    total=rows_in,
                )
            )
        )
        actual = _count(con, silver_path(silver_dir, table, month))
        results.append(
            _finish(
                QualityCheck(
                    f"{name}_silver_parquet_x_manifesto",
                    table,
                    desc_c,
                    True,
                    violations=abs(actual - rows_out),
                    total=rows_out,
                )
            )
        )
        quarantined = _count(con, quarantine_path(silver_dir, table, month))
        results.append(
            _finish(
                QualityCheck(
                    f"{name}_quarentena_x_manifesto",
                    table,
                    desc_d,
                    True,
                    violations=abs(quarantined - removed),
                    total=removed,
                )
            )
        )

    if bronze_manifest:
        results.append(
            _finish(
                QualityCheck(
                    "bronze_linhas_rejeitadas",
                    "Bronze",
                    "Linhas rejeitadas na leitura dos CSV (formato inválido)",
                    False,
                    violations=int(bronze_manifest.get("total_rejected_lines", 0)),
                    total=int(bronze_manifest.get("total_rows", 0)),
                )
            )
        )
    else:
        skipped(
            "bronze_linhas_rejeitadas",
            "Bronze",
            "Linhas rejeitadas na leitura dos CSV",
            "sem manifesto da bronze",
            False,
        )

    desc_g = "Verificações bloqueantes da gold passaram e todos os modelos existem"
    if gold_manifest:
        failed = [c for c in gold_manifest.get("checks", []) if c.get("blocking") and not c.get("ok")]
        missing = [m for m in MODEL_NAMES if m not in gold_manifest.get("models", {})]
        results.append(
            _finish(
                QualityCheck(
                    "gold_verificacoes_bloqueantes",
                    "Gold",
                    desc_g,
                    True,
                    violations=len(failed) + len(missing),
                    total=len(MODEL_NAMES),
                )
            )
        )
    else:
        skipped("gold_verificacoes_bloqueantes", "Gold", desc_g, "sem manifesto da gold")
    return results


def benchmarks(
    bronze_manifest: dict | None, silver_manifest: dict | None, gold_manifest: dict | None
) -> dict:
    """Tempos e volumes por camada, lidos dos manifestos de cada execucao."""
    out: dict = {"bronze": {}, "silver": {}, "gold": {}}
    for entry in (bronze_manifest or {}).get("files", []):
        item = out["bronze"].setdefault(entry["table"], {"linhas": 0, "bytes": 0, "segundos": 0.0})
        item["linhas"] += int(entry["rows"])
        item["bytes"] += int(entry["parquet_bytes"])
        item["segundos"] = round(item["segundos"] + float(entry["seconds"]), 2)
    for table, entry in (silver_manifest or {}).get("tables", {}).items():
        out["silver"][table] = {
            "linhas": int(entry["rows_out"]),
            "bytes": int(entry["parquet_bytes"]),
            "segundos": float(entry["seconds"]),
        }
    for model, entry in (gold_manifest or {}).get("models", {}).items():
        out["gold"][model] = {
            "linhas": int(entry["rows"]),
            "bytes": int(entry["parquet_bytes"]),
            "segundos": float(entry["seconds"]),
        }
    return out


@dataclass
class QualityRun:
    month: str
    checks: list[QualityCheck] = field(default_factory=list)
    seconds_by_step: dict[str, float] = field(default_factory=dict)
    total_seconds: float = 0.0
    peak_memory_mib: int | None = None
    machine: dict = field(default_factory=dict)
    benchmarks: dict = field(default_factory=dict)
    quick: bool = False
    memory_limit: str = ""


def write_manifest(quality_dir: Path, result: QualityRun) -> Path:
    path = quality_dir / f"_manifest_{result.month}.json"
    manifest = {
        "reference_month": result.month,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "quick": result.quick,
        "memory_limit": result.memory_limit,
        "total_seconds": result.total_seconds,
        "peak_memory_mib": result.peak_memory_mib,
        "machine": result.machine,
        "checks": [asdict(c) for c in result.checks],
        "seconds_by_step": result.seconds_by_step,
        "benchmarks": result.benchmarks,
    }
    quality_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _dec(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _mib(value: int) -> str:
    return _dec(value / (1024 * 1024))


def _fmt(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def render_report(result: QualityRun) -> str:
    counts = {
        s: sum(1 for c in result.checks if c.status == s)
        for s in (STATUS_OK, STATUS_WARNING, STATUS_FAILED, STATUS_SKIPPED)
    }
    lines = [
        f"# Qualidade de dados e tempos ({result.month})",
        "",
        "Este relatório contém apenas contagens. Uma verificação bloqueante faz o comando falhar; "
        "uma informativa só registra a contagem.",
        "",
        f"Resumo: {counts[STATUS_OK]} ok, {counts[STATUS_WARNING]} com aviso, "
        f"{counts[STATUS_FAILED]} falharam, {counts[STATUS_SKIPPED]} puladas. "
        f"Execução {'rápida (sem as verificações pesadas)' if result.quick else 'completa'} "
        f"em {_dec(result.total_seconds)} s.",
        "",
        "## Verificações bloqueantes",
        "",
        "| Verificação | Tabela | Resultado | Violações | Linhas avaliadas |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for c in result.checks:
        if c.blocking:
            lines.append(f"| `{c.name}` | {c.table} | {c.status} | {_fmt(c.violations)} | {_fmt(c.total)} |")
    lines += [
        "",
        "## Verificações informativas",
        "",
        "| Verificação | Tabela | O que confere | Violações | Linhas avaliadas | Proporção |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for c in result.checks:
        if c.blocking:
            continue
        ratio = f"{100 * c.violations / c.total:.4f}%".replace(".", ",") if c.total else "-"
        if c.status == STATUS_SKIPPED:
            lines.append(f"| `{c.name}` | {c.table} | {c.description} | pulada | - | - |")
        else:
            lines.append(
                f"| `{c.name}` | {c.table} | {c.description} | "
                f"{_fmt(c.violations)} | {_fmt(c.total)} | {ratio} |"
            )
    lines += [
        "",
        "## Tempos medidos por camada",
        "",
        "Lidos dos manifestos de cada execução (bronze, silver e gold), na máquina em que cada uma rodou.",
        "",
    ]
    for layer in ("bronze", "silver", "gold"):
        items = result.benchmarks.get(layer) or {}
        if not items:
            continue
        lines += [
            f"### {layer.capitalize()}",
            "",
            "| Item | Linhas | Parquet (MiB) | Tempo (s) | Linhas por segundo |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        total_rows = total_bytes = 0
        total_seconds = 0.0
        for name, m in sorted(items.items(), key=lambda kv: -kv[1]["segundos"]):
            rate = _fmt(int(m["linhas"] / m["segundos"])) if m["segundos"] >= 1 else "-"
            lines.append(
                f"| `{name}` | {_fmt(m['linhas'])} | {_mib(m['bytes'])} | {_dec(m['segundos'])} | {rate} |"
            )
            total_rows += m["linhas"]
            total_bytes += m["bytes"]
            total_seconds += m["segundos"]
        lines.append(f"| **Total** | {_fmt(total_rows)} | {_mib(total_bytes)} | {_dec(total_seconds)} | |")
        lines.append("")
    lines += ["## Máquina desta verificação", "", "| Item | Valor |", "| --- | --- |"]
    machine = result.machine
    ram = machine.get("memoria_total_mib")
    peak = f"{result.peak_memory_mib} MiB" if result.peak_memory_mib else "não disponível"
    lines += [
        f"| Sistema | {machine.get('sistema')} |",
        f"| Processador | {machine.get('processador') or 'não disponível'} |",
        f"| CPUs lógicas | {machine.get('cpus_logicas')} |",
        f"| Memória total | {f'{ram} MiB' if ram else 'não disponível'} |",
        f"| Python / DuckDB | {machine.get('python')} / {machine.get('duckdb')} |",
        f"| Limite de memória do DuckDB | {result.memory_limit} |",
        f"| Pico de memória do processo | {peak} |",
        "",
        "O pico de memória e a máquina se referem apenas a esta verificação, "
        "não às execuções que geraram as camadas.",
        "",
    ]
    return "\n".join(lines)


def run(
    month: str,
    *,
    bronze_dir: Path,
    silver_dir: Path,
    gold_dir: Path,
    quality_dir: Path,
    report_dir: Path | None,
    quick: bool,
    memory_limit: str,
    threads: int | None,
    tmp_root: Path,
) -> int:
    available = {t for t in config.ALL_TABLES if silver_path(silver_dir, t, month).exists()}
    if not available:
        log.error("Nada para verificar. Rode antes: python -m cnpj_pipeline.silver --month %s", month)
        return 1

    tmp_root.mkdir(parents=True, exist_ok=True)
    options: dict[str, str | int] = {
        "memory_limit": memory_limit,
        "temp_directory": str(tmp_root / "duckdb_spill"),
    }
    if threads:
        options["threads"] = threads
    con = duckdb.connect(config=options)
    started = time.monotonic()
    result = QualityRun(month=month, quick=quick, memory_limit=memory_limit, machine=machine_info())
    try:
        register_macros(con)
        for table in available:
            path = silver_path(silver_dir, table, month)
            con.execute(
                f"CREATE OR REPLACE VIEW {_view_name(table)} AS "
                f"SELECT * FROM read_parquet({_q(path.as_posix())}, hive_partitioning=false)"
            )
        row_results, row_seconds = run_row_rules(con, month, available)
        set_results, set_seconds = run_set_checks(con, available, quick=quick)

        bronze_manifest = _read_json(bronze_dir / f"_manifest_{month}.json")
        silver_manifest = _read_json(silver_dir / f"_manifest_{month}.json")
        gold_manifest = _read_json(gold_dir / f"_manifest_{month}.json")
        layer_results = run_layer_checks(
            con,
            month,
            available,
            bronze_manifest=bronze_manifest,
            silver_manifest=silver_manifest,
            gold_manifest=gold_manifest,
            silver_dir=silver_dir,
        )
        result.checks = row_results + set_results + layer_results
        result.seconds_by_step = {**{f"regras_{k.lower()}": v for k, v in row_seconds.items()}, **set_seconds}
        result.benchmarks = benchmarks(bronze_manifest, silver_manifest, gold_manifest)
    finally:
        con.close()
    result.total_seconds = round(time.monotonic() - started, 1)
    result.peak_memory_mib = peak_memory_mib()

    manifest_path = write_manifest(quality_dir, result)
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{month}.md").write_text(render_report(result), encoding="utf-8")

    failed = [c for c in result.checks if c.status == STATUS_FAILED]
    warnings = [c for c in result.checks if c.status == STATUS_WARNING]
    skipped = [c for c in result.checks if c.status == STATUS_SKIPPED]
    log.info(
        "Qualidade %s: %d ok, %d aviso(s), %d falha(s), %d pulada(s) em %.1f s. Manifesto: %s",
        month,
        len(result.checks) - len(failed) - len(warnings) - len(skipped),
        len(warnings),
        len(failed),
        len(skipped),
        result.total_seconds,
        manifest_path,
    )
    for c in warnings:
        log.warning("aviso %s (%s): %d de %d linhas", c.name, c.table, c.violations, c.total)
    for c in failed:
        log.error(
            "FALHOU %s (%s): %d violacao(oes), tolerancia %d", c.name, c.table, c.violations, c.tolerance
        )
    if failed:
        log.error("Verificacao bloqueante falhou: nao publique este mes.")
        return 1
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.quality",
        description="Verifica a qualidade dos dados da silver e da gold e registra os tempos medidos.",
    )
    parser.add_argument("--month", required=True, help="Mes de referencia no formato AAAA-MM")
    parser.add_argument("--bronze-dir", type=Path, default=config.BRONZE_DIR)
    parser.add_argument("--silver-dir", type=Path, default=config.SILVER_DIR)
    parser.add_argument("--gold-dir", type=Path, default=config.GOLD_DIR)
    parser.add_argument("--quality-dir", type=Path, default=config.DATA_DIR / "quality")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=config.PROJECT_ROOT / "docs" / "quality",
        help="Pasta do relatorio de contagens (padrao: docs/quality)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Pula as verificacoes pesadas (chaves unicas das tabelas grandes e as juncoes entre tabelas)",
    )
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
    return run(
        args.month,
        bronze_dir=args.bronze_dir,
        silver_dir=args.silver_dir,
        gold_dir=args.gold_dir,
        quality_dir=args.quality_dir,
        report_dir=args.report_dir,
        quick=args.quick,
        memory_limit=args.memory_limit,
        threads=args.threads,
        tmp_root=config.DATA_DIR / "tmp",
    )


if __name__ == "__main__":
    sys.exit(main())
