"""Exporta agregados da gold para JSON, a base do site publico.

O site estatico nao tem acesso a AWS nem a chave de modelo, entao tudo o que ele mostra sem o agente
vem destes arquivos. Por isso a exportacao e conservadora:

- le somente os marts e as dimensoes da gold, nunca `fato_estabelecimento` (uma linha por CNPJ);
- recusa-se a rodar se alguma reconciliacao bloqueante da gold falhou;
- descarta recortes com contagem abaixo de MIN_CELL e informa quantos foram descartados;
- municipio so aparece no ranking dos maiores, nunca cruzado com divisao da CNAE;
- a saida nao tem data nem hora, entao rodar duas vezes gera arquivos identicos byte a byte.

A supressao de celulas pequenas nao e controle de divulgacao: o cadastro de CNPJ ja e publico por
estabelecimento. Ela evita que o site destaque contagens minusculas e instaveis, e mantem a regra de
publicacao igual a do agente. Os totais nacionais continuam completos, entao um valor suprimido pode ser
estimado por diferenca; o arquivo nao tenta esconder isso.

Uso:
    python -m cnpj_pipeline.site_export --month 2026-10 --gold-dir data/gold --out site/public/data
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from cnpj_pipeline import config
from cnpj_pipeline.download import MONTH_RE
from cnpj_pipeline.gold import gold_path

log = logging.getLogger("cnpj_pipeline.site_export")

SCHEMA_VERSION = 1

# Contagem minima de um recorte para ser publicado.
MIN_CELL = 5

# Primeiro ano das series anuais e das series mensais e recortes por secao e UF.
ANNUAL_START_YEAR = 2000
MONTHLY_START_YEAR = 2015

TOP_MUNICIPIOS = 50
TOP_UFS = 5
TOP_NATUREZAS = 12
TOP_QUALIFICACOES = 15
CAPITAL_NATUREZAS = 5

# Limite de tamanho de cada arquivo, para o site carregar rapido e o repositorio nao inchar.
MAX_FILE_BYTES = 150_000

MARTS = (
    "mart_aberturas_mensal",
    "mart_estabelecimentos_ativos",
    "mart_perfil_porte_natureza",
    "mart_simples_mei_uf",
    "mart_socios_qualificacao",
)
DIMS = ("dim_cnae_secao",)
SOURCES = MARTS + DIMS

UF_NOMES: dict[str, str] = {
    "AC": "Acre",
    "AL": "Alagoas",
    "AM": "Amazonas",
    "AP": "Amapá",
    "BA": "Bahia",
    "CE": "Ceará",
    "DF": "Distrito Federal",
    "ES": "Espírito Santo",
    "GO": "Goiás",
    "MA": "Maranhão",
    "MG": "Minas Gerais",
    "MS": "Mato Grosso do Sul",
    "MT": "Mato Grosso",
    "PA": "Pará",
    "PB": "Paraíba",
    "PE": "Pernambuco",
    "PI": "Piauí",
    "PR": "Paraná",
    "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte",
    "RO": "Rondônia",
    "RR": "Roraima",
    "RS": "Rio Grande do Sul",
    "SC": "Santa Catarina",
    "SE": "Sergipe",
    "SP": "São Paulo",
    "TO": "Tocantins",
    "EX": "Exterior",
}

# Ressalvas que o site mostra ao lado dos numeros. Texto fixo, escrito aqui, nunca vindo do cadastro.
RESSALVAS: dict[str, str] = {
    "foto_unica": (
        "O cadastro é uma foto de um único mês. Estabelecimentos aparecem com a situação que tinham no "
        "mês de referência."
    ),
    "org_politica": (
        "O CNAE 9492800 (organizações políticas) concentra picos de aberturas em anos de eleição "
        "municipal. As séries de aberturas separam esse grupo; a causa dos picos é provável, não demonstrada."
    ),
    "mes_parcial": (
        "O ano e o mês de referência estão incompletos: registros recentes ainda podem chegar ao cadastro."
    ),
    "celulas_pequenas": (
        f"Recortes com menos de {MIN_CELL} unidades não são publicados. O número de recortes omitidos "
        "aparece em cada arquivo."
    ),
    "capital_social": (
        "O capital social é declarado e tem valores extremos. Por isso o site usa medianas e não somas."
    ),
    "aberturas_historicas": (
        "A série de aberturas só conta estabelecimentos que ainda constam no cadastro; datas muito antigas "
        "podem ser erros de preenchimento."
    ),
}

FILES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # nome: (titulo, descricao, ressalvas)
    "overview": (
        "Visão geral",
        "Totais nacionais do cadastro ativo e das aberturas.",
        ("foto_unica", "org_politica"),
    ),
    "uf": (
        "Empresas e estabelecimentos ativos por UF",
        "Contagens por UF e participação de Simples e MEI entre as empresas ativas.",
        ("foto_unica", "org_politica", "celulas_pequenas"),
    ),
    "aberturas_ano": (
        "Aberturas por ano",
        "Estabelecimentos por ano de início de atividade, separando organizações políticas.",
        ("org_politica", "mes_parcial", "aberturas_historicas"),
    ),
    "aberturas_mes": (
        "Aberturas por mês",
        "Estabelecimentos por mês de início de atividade, separando organizações políticas.",
        ("org_politica", "mes_parcial", "aberturas_historicas"),
    ),
    "aberturas_secao_ano": (
        "Aberturas por seção da CNAE e ano",
        "Aberturas sem o CNAE de organizações políticas, por seção da CNAE.",
        ("org_politica", "mes_parcial", "celulas_pequenas", "aberturas_historicas"),
    ),
    "aberturas_uf_ano": (
        "Aberturas por UF e ano",
        "Aberturas sem o CNAE de organizações políticas, por UF.",
        ("org_politica", "mes_parcial", "celulas_pequenas", "aberturas_historicas"),
    ),
    "ativos_secao": (
        "Estabelecimentos ativos por seção da CNAE",
        "Estabelecimentos ativos e optantes pelo Simples e pelo MEI, por seção.",
        ("foto_unica", "celulas_pequenas"),
    ),
    "municipios_top": (
        f"Os {TOP_MUNICIPIOS} municípios com mais estabelecimentos ativos",
        "Ranking de municípios. Município nunca é cruzado com atividade econômica.",
        ("foto_unica",),
    ),
    "perfil_porte": (
        "Empresas ativas por porte",
        "Empresas ativas (uma por empresa) por porte.",
        ("foto_unica",),
    ),
    "perfil_natureza": (
        "Empresas ativas por natureza jurídica",
        f"As {TOP_NATUREZAS} naturezas mais comuns, o restante agrupado, e os totais por grupo.",
        ("foto_unica",),
    ),
    "capital_mediano": (
        "Capital social mediano",
        "Mediana do capital social declarado por porte e natureza, sem organizações políticas.",
        ("capital_social", "celulas_pequenas"),
    ),
    "socios": (
        "Sócios por qualificação e tipo",
        f"Vínculos de sócio nas {TOP_QUALIFICACOES} qualificações mais comuns.",
        ("celulas_pequenas",),
    ),
    "pipeline": (
        "O pipeline em números",
        "Contagens e tempos das camadas bronze, silver e gold e dos testes de qualidade.",
        (),
    ),
}

# Chaves permitidas em qualquer arquivo de dados. Um teste garante que nada fora desta lista sai.
ALLOWED_KEYS = frozenset(
    {
        # envelope
        "arquivo", "titulo", "descricao", "mes_referencia", "esquema", "ressalvas", "celulas_suprimidas",
        "dados", "ano_parcial", "mes_parcial", "secoes", "tipos", "grupos", "outras",
        # recortes
        "ano", "ano_mes", "uf", "uf_nome", "secao", "secao_descricao", "municipio_nome", "qualificacao",
        "tipo", "porte", "porte_descricao", "natureza", "natureza_descricao", "natureza_grupo", "grupo",
        "codigo", "parcial",
        # medidas
        "estabelecimentos_ativos", "matrizes_ativas", "empresas_ativas", "empresas_ativas_org_politica",
        "optantes_simples", "optantes_mei", "pct_simples", "pct_mei", "estabelecimentos_abertos",
        "abertos_org_politica", "abertos_demais", "aberturas", "empresas", "participacao_pct",
        "capital_social_mediano", "socios", "posicao",
        # visao geral
        "estabelecimentos_no_cadastro", "aberturas_org_politica", "aberturas_antes_do_inicio_da_serie",
        "ufs", "maior_uf", "participacao_maior_uf_pct", "participacao_cinco_maiores_ufs_pct",
        "todas", "sem_org_politica", "total_socios",
        # pipeline
        "bronze", "silver", "gold", "qualidade", "tabelas", "linhas", "linhas_entrada", "linhas_saida",
        "chaves_invalidas", "duplicadas_removidas", "linhas_rejeitadas", "bytes_csv", "bytes_parquet",
        "modelos", "reconciliacoes", "nome", "ok", "bloqueante", "detalhe", "avisos",
        "verificacoes", "verificacoes_bloqueantes", "segundos", "memoria_pico_mib", "limite_memoria",
        "amostra", "etapas",
        # indice
        "meses", "ultimo", "arquivos", "bytes", "sha256", "fonte",
    }
)  # fmt: skip


class ExportError(RuntimeError):
    """Entrada insuficiente ou insegura para publicar."""


# --------------------------------------------------------------------------- #
# Entrada
# --------------------------------------------------------------------------- #


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _connect(gold_dir: Path, month: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for name in SOURCES:
        path = gold_path(gold_dir, name, month)
        if not path.exists():
            con.close()
            raise ExportError(f"modelo da gold ausente: {path}")
        source = f"read_parquet({_q(path.as_posix())}, hive_partitioning=false)"
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM {source}")
    return con


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def rows(con: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cur = con.execute(sql, list(params))
    names = [d[0] for d in cur.description]
    return [{n: _plain(v) for n, v in zip(names, r, strict=True)} for r in cur.fetchall()]


def one(con: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] = ()) -> dict[str, Any]:
    result = rows(con, sql, params)
    return result[0] if result else {}


def read_manifest(directory: Path, month: str) -> dict | None:
    path = directory / f"_manifest_{month}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def check_gold(manifest: dict | None, month: str) -> dict:
    """Recusa publicar se a gold nao tem manifesto ou se alguma reconciliacao bloqueante falhou."""
    if manifest is None:
        raise ExportError(f"manifesto da gold ausente para {month}; rode a gold antes de exportar")
    if manifest.get("reference_month") != month:
        raise ExportError("o manifesto da gold e de outro mes")
    failed = [c["name"] for c in manifest.get("checks", []) if c.get("blocking") and not c.get("ok")]
    if failed:
        raise ExportError("reconciliacoes bloqueantes falharam: " + ", ".join(sorted(failed)))
    if not manifest.get("checks"):
        raise ExportError("a gold nao tem reconciliacoes registradas")
    return manifest


# --------------------------------------------------------------------------- #
# Utilitarios de saida
# --------------------------------------------------------------------------- #


def pct(part: int | float, whole: int | float, digits: int = 2) -> float | None:
    if not whole:
        return None
    return round(100.0 * part / whole, digits)


def suppress(items: list[dict[str, Any]], key: str, min_cell: int) -> tuple[list[dict[str, Any]], int]:
    """Remove os recortes cuja contagem `key` e menor que `min_cell`. Devolve (mantidos, omitidos)."""
    kept = [r for r in items if r[key] is not None and r[key] >= min_cell]
    return kept, len(items) - len(kept)


def envelope(
    name: str, month: str, data: Any, *, suppressed: int | None = None, **extra: Any
) -> dict[str, Any]:
    title, description, caveats = FILES[name]
    doc: dict[str, Any] = {
        "arquivo": name,
        "esquema": SCHEMA_VERSION,
        "mes_referencia": month,
        "titulo": title,
        "descricao": description,
        "ressalvas": {k: RESSALVAS[k] for k in caveats},
    }
    if suppressed is not None:
        doc["celulas_suprimidas"] = suppressed
    doc.update(extra)
    doc["dados"] = data
    return doc


def dumps(doc: Any) -> str:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"


# Objetos cujas chaves sao dados e nao esquema (nomes de verificacoes, etapas, ressalvas).
DYNAMIC_KEYS = frozenset({"detalhe", "etapas", "ressalvas"})


def collect_keys(value: Any) -> set[str]:
    """Chaves de objetos em qualquer profundidade, sem descer nos objetos de chaves dinamicas."""
    found: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(k)
            if k not in DYNAMIC_KEYS:
                found |= collect_keys(v)
    elif isinstance(value, list):
        for v in value:
            found |= collect_keys(v)
    return found


def check_dynamic(name: str, value: Any) -> None:
    """Os objetos de chaves dinamicas so podem conter ressalvas conhecidas e numeros."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "ressalvas" and not (isinstance(v, dict) and set(v) <= set(RESSALVAS)):
                raise ExportError(f"{name}: ressalva desconhecida")
            if k in ("detalhe", "etapas") and not all(
                isinstance(x, int | float) and not isinstance(x, bool) for x in v.values()
            ):
                raise ExportError(f"{name}: {k} so pode conter numeros")
            check_dynamic(name, v)
    elif isinstance(value, list):
        for v in value:
            check_dynamic(name, v)


# --------------------------------------------------------------------------- #
# Arquivos de dados
# --------------------------------------------------------------------------- #


def build_overview(con: duckdb.DuckDBPyConnection, month: str, min_cell: int) -> dict:
    ativos = one(
        con, "SELECT coalesce(sum(estabelecimentos_ativos), 0) AS n FROM mart_estabelecimentos_ativos"
    )["n"]
    simples = one(
        con,
        """
        SELECT
            coalesce(sum(empresas_ativas), 0) AS empresas,
            coalesce(sum(empresas_ativas) FILTER (WHERE is_organizacao_politica), 0) AS empresas_pol,
            coalesce(sum(optantes_simples), 0) AS simples,
            coalesce(sum(optantes_simples) FILTER (WHERE is_organizacao_politica), 0) AS simples_pol,
            coalesce(sum(optantes_mei), 0) AS mei,
            coalesce(sum(optantes_mei) FILTER (WHERE is_organizacao_politica), 0) AS mei_pol
        FROM mart_simples_mei_uf
        """,
    )
    aberturas = one(
        con,
        """
        SELECT
            coalesce(sum(estabelecimentos_abertos), 0) AS total,
            coalesce(sum(estabelecimentos_abertos) FILTER (WHERE is_organizacao_politica), 0) AS pol,
            coalesce(sum(estabelecimentos_abertos) FILTER (WHERE ano < ?), 0) AS antes
        FROM mart_aberturas_mensal
        """,
        [ANNUAL_START_YEAR],
    )
    ufs = rows(
        con,
        """
        SELECT uf, sum(estabelecimentos_ativos) AS n FROM mart_estabelecimentos_ativos
        WHERE uf IS NOT NULL
        GROUP BY uf ORDER BY n DESC, uf
        """,
    )
    socios = one(con, "SELECT coalesce(sum(socios), 0) AS n FROM mart_socios_qualificacao")["n"]
    empresas, emp_pol = simples["empresas"], simples["empresas_pol"]
    sem = {
        "empresas_ativas": empresas - emp_pol,
        "optantes_simples": simples["simples"] - simples["simples_pol"],
        "optantes_mei": simples["mei"] - simples["mei_pol"],
    }
    todas = {
        "empresas_ativas": empresas,
        "optantes_simples": simples["simples"],
        "optantes_mei": simples["mei"],
    }
    for group in (todas, sem):
        group["pct_simples"] = pct(group["optantes_simples"], group["empresas_ativas"])
        group["pct_mei"] = pct(group["optantes_mei"], group["empresas_ativas"])
    top = ufs[0] if ufs else {"uf": None, "n": 0}
    data = {
        "estabelecimentos_ativos": ativos,
        "empresas_ativas_org_politica": emp_pol,
        "todas": todas,
        "sem_org_politica": sem,
        "estabelecimentos_no_cadastro": aberturas["total"],
        "aberturas_org_politica": aberturas["pol"],
        "aberturas_antes_do_inicio_da_serie": aberturas["antes"],
        "total_socios": socios,
        "ufs": len(ufs),
        "maior_uf": top["uf"],
        "participacao_maior_uf_pct": pct(top["n"], ativos),
        "participacao_cinco_maiores_ufs_pct": pct(sum(u["n"] for u in ufs[:TOP_UFS]), ativos),
    }
    return envelope("overview", month, data)


def build_uf(con: duckdb.DuckDBPyConnection, month: str, min_cell: int) -> dict:
    est = {
        r["uf"]: r
        for r in rows(
            con,
            """
            SELECT uf, sum(estabelecimentos_ativos) AS estabelecimentos_ativos,
                   sum(matrizes_ativas) AS matrizes_ativas
            FROM mart_estabelecimentos_ativos GROUP BY uf
            """,
        )
    }
    items = []
    for r in rows(
        con,
        """
        SELECT uf, sum(empresas_ativas) AS empresas_ativas,
               sum(empresas_ativas) FILTER (WHERE is_organizacao_politica) AS empresas_ativas_org_politica,
               sum(optantes_simples) AS optantes_simples, sum(optantes_mei) AS optantes_mei
        FROM mart_simples_mei_uf GROUP BY uf ORDER BY uf
        """,
    ):
        e = est.get(r["uf"], {})
        items.append(
            {
                "uf": r["uf"],
                "uf_nome": UF_NOMES.get(r["uf"]),
                "estabelecimentos_ativos": e.get("estabelecimentos_ativos", 0),
                "matrizes_ativas": e.get("matrizes_ativas", 0),
                "empresas_ativas": r["empresas_ativas"],
                "empresas_ativas_org_politica": r["empresas_ativas_org_politica"] or 0,
                "optantes_simples": r["optantes_simples"],
                "optantes_mei": r["optantes_mei"],
                "pct_simples": pct(r["optantes_simples"], r["empresas_ativas"]),
                "pct_mei": pct(r["optantes_mei"], r["empresas_ativas"]),
            }
        )
    kept, omitted = suppress(items, "empresas_ativas", min_cell)
    kept.sort(key=lambda r: (-r["estabelecimentos_ativos"], r["uf"] or ""))
    return envelope("uf", month, kept, suppressed=omitted)


def _year_of(month: str) -> int:
    return int(month[:4])


def build_aberturas_ano(con: duckdb.DuckDBPyConnection, month: str, min_cell: int) -> dict:
    data = rows(
        con,
        """
        SELECT ano,
               sum(estabelecimentos_abertos) AS estabelecimentos_abertos,
               coalesce(sum(estabelecimentos_abertos) FILTER (WHERE is_organizacao_politica), 0)
                   AS abertos_org_politica,
               coalesce(sum(estabelecimentos_abertos) FILTER (WHERE NOT is_organizacao_politica), 0)
                   AS abertos_demais
        FROM mart_aberturas_mensal
        WHERE ano >= ?
        GROUP BY ano ORDER BY ano
        """,
        [ANNUAL_START_YEAR],
    )
    kept, omitted = suppress(data, "estabelecimentos_abertos", min_cell)
    year = _year_of(month)
    for r in kept:
        r["parcial"] = r["ano"] == year
    return envelope("aberturas_ano", month, kept, suppressed=omitted, ano_parcial=year)


def build_aberturas_mes(con: duckdb.DuckDBPyConnection, month: str, min_cell: int) -> dict:
    data = rows(
        con,
        """
        SELECT ano_mes,
               coalesce(sum(estabelecimentos_abertos) FILTER (WHERE NOT is_organizacao_politica), 0)
                   AS abertos_demais,
               coalesce(sum(estabelecimentos_abertos) FILTER (WHERE is_organizacao_politica), 0)
                   AS abertos_org_politica,
               sum(estabelecimentos_abertos) AS estabelecimentos_abertos
        FROM mart_aberturas_mensal
        WHERE ano >= ?
        GROUP BY ano_mes ORDER BY ano_mes
        """,
        [MONTHLY_START_YEAR],
    )
    kept, omitted = suppress(data, "estabelecimentos_abertos", min_cell)
    for r in kept:
        r["parcial"] = r["ano_mes"] == month
    return envelope("aberturas_mes", month, kept, suppressed=omitted, mes_parcial=month)


def build_aberturas_secao_ano(con: duckdb.DuckDBPyConnection, month: str, min_cell: int) -> dict:
    data = rows(
        con,
        """
        SELECT a.ano, a.cnae_secao AS secao, sum(a.estabelecimentos_abertos) AS aberturas
        FROM mart_aberturas_mensal a
        WHERE a.ano >= ? AND NOT a.is_organizacao_politica
        GROUP BY a.ano, a.cnae_secao ORDER BY a.ano, a.cnae_secao NULLS LAST
        """,
        [MONTHLY_START_YEAR],
    )
    secoes = rows(con, "SELECT codigo, descricao FROM dim_cnae_secao ORDER BY codigo")
    kept, omitted = suppress(data, "aberturas", min_cell)
    return envelope(
        "aberturas_secao_ano", month, kept, suppressed=omitted, ano_parcial=_year_of(month), secoes=secoes
    )


def build_aberturas_uf_ano(con: duckdb.DuckDBPyConnection, month: str, min_cell: int) -> dict:
    data = rows(
        con,
        """
        SELECT ano, uf, sum(estabelecimentos_abertos) AS aberturas
        FROM mart_aberturas_mensal
        WHERE ano >= ? AND NOT is_organizacao_politica
        GROUP BY ano, uf ORDER BY ano, uf
        """,
        [MONTHLY_START_YEAR],
    )
    kept, omitted = suppress(data, "aberturas", min_cell)
    return envelope("aberturas_uf_ano", month, kept, suppressed=omitted, ano_parcial=_year_of(month))


def build_ativos_secao(con: duckdb.DuckDBPyConnection, month: str, min_cell: int) -> dict:
    data = rows(
        con,
        """
        SELECT cnae_secao AS secao, max(cnae_secao_descricao) AS secao_descricao,
               sum(estabelecimentos_ativos) AS estabelecimentos_ativos,
               sum(matrizes_ativas) AS matrizes_ativas,
               sum(optantes_simples) AS optantes_simples, sum(optantes_mei) AS optantes_mei
        FROM mart_estabelecimentos_ativos
        GROUP BY cnae_secao ORDER BY estabelecimentos_ativos DESC, cnae_secao NULLS LAST
        """,
    )
    kept, omitted = suppress(data, "estabelecimentos_ativos", min_cell)
    return envelope("ativos_secao", month, kept, suppressed=omitted)


def build_municipios_top(con: duckdb.DuckDBPyConnection, month: str, min_cell: int) -> dict:
    data = rows(
        con,
        """
        SELECT uf, municipio_nome, sum(estabelecimentos_ativos) AS estabelecimentos_ativos,
               sum(matrizes_ativas) AS matrizes_ativas
        FROM mart_estabelecimentos_ativos
        GROUP BY uf, municipio, municipio_nome
        ORDER BY estabelecimentos_ativos DESC, uf, municipio_nome, municipio
        LIMIT ?
        """,
        [TOP_MUNICIPIOS],
    )
    kept, omitted = suppress(data, "estabelecimentos_ativos", min_cell)
    for position, r in enumerate(kept, start=1):
        r["posicao"] = position
    return envelope("municipios_top", month, kept, suppressed=omitted)


def build_perfil_porte(con: duckdb.DuckDBPyConnection, month: str, min_cell: int) -> dict:
    data = rows(
        con,
        """
        SELECT porte_empresa AS porte, max(porte_descricao) AS porte_descricao,
               sum(empresas_ativas) AS empresas_ativas
        FROM mart_perfil_porte_natureza
        GROUP BY porte_empresa ORDER BY empresas_ativas DESC, porte_empresa
        """,
    )
    total = sum(r["empresas_ativas"] for r in data)
    for r in data:
        r["participacao_pct"] = pct(r["empresas_ativas"], total)
    kept, omitted = suppress(data, "empresas_ativas", min_cell)
    return envelope("perfil_porte", month, kept, suppressed=omitted)


def build_perfil_natureza(con: duckdb.DuckDBPyConnection, month: str, min_cell: int) -> dict:
    todas = rows(
        con,
        """
        SELECT natureza_juridica AS natureza, max(natureza_descricao) AS natureza_descricao,
               max(natureza_grupo) AS natureza_grupo, sum(empresas_ativas) AS empresas_ativas
        FROM mart_perfil_porte_natureza
        GROUP BY natureza_juridica ORDER BY empresas_ativas DESC, natureza_juridica
        """,
    )
    total = sum(r["empresas_ativas"] for r in todas)
    top = todas[:TOP_NATUREZAS]
    resto = sum(r["empresas_ativas"] for r in todas[TOP_NATUREZAS:])
    for r in top:
        r["participacao_pct"] = pct(r["empresas_ativas"], total)
    kept, omitted = suppress(top, "empresas_ativas", min_cell)
    grupos = rows(
        con,
        """
        SELECT natureza_grupo AS grupo, sum(empresas_ativas) AS empresas_ativas
        FROM mart_perfil_porte_natureza
        GROUP BY natureza_grupo ORDER BY empresas_ativas DESC, natureza_grupo NULLS LAST
        """,
    )
    for g in grupos:
        g["participacao_pct"] = pct(g["empresas_ativas"], total)
    outras = {"empresas_ativas": resto, "participacao_pct": pct(resto, total)}
    return envelope("perfil_natureza", month, kept, suppressed=omitted, grupos=grupos, outras=outras)


def build_capital_mediano(con: duckdb.DuckDBPyConnection, month: str, min_cell: int) -> dict:
    data = rows(
        con,
        """
        WITH top AS (
            SELECT natureza_juridica FROM mart_perfil_porte_natureza
            GROUP BY natureza_juridica ORDER BY sum(empresas_ativas) DESC, natureza_juridica LIMIT ?
        )
        SELECT porte_empresa AS porte, porte_descricao, natureza_juridica AS natureza,
               natureza_descricao, empresas_ativas AS empresas, capital_social_mediano
        FROM mart_perfil_porte_natureza
        WHERE NOT is_organizacao_politica AND capital_social_mediano IS NOT NULL
          AND natureza_juridica IN (SELECT natureza_juridica FROM top)
        ORDER BY natureza_juridica, porte_empresa
        """,
        [CAPITAL_NATUREZAS],
    )
    kept, omitted = suppress(data, "empresas", min_cell)
    return envelope("capital_mediano", month, kept, suppressed=omitted)


def build_socios(con: duckdb.DuckDBPyConnection, month: str, min_cell: int) -> dict:
    tipos = rows(
        con,
        """
        SELECT identificador_socio AS codigo, max(tipo_socio) AS descricao, sum(socios) AS socios
        FROM mart_socios_qualificacao GROUP BY identificador_socio ORDER BY identificador_socio
        """,
    )
    data = rows(
        con,
        """
        WITH top AS (
            SELECT qualificacao_socio FROM mart_socios_qualificacao
            GROUP BY qualificacao_socio ORDER BY sum(socios) DESC, qualificacao_socio LIMIT ?
        )
        SELECT qualificacao_socio AS qualificacao, qualificacao_descricao AS descricao,
               identificador_socio AS tipo, socios
        FROM mart_socios_qualificacao
        WHERE qualificacao_socio IN (SELECT qualificacao_socio FROM top)
        ORDER BY qualificacao_socio, identificador_socio
        """,
        [TOP_QUALIFICACOES],
    )
    kept, omitted = suppress(data, "socios", min_cell)
    return envelope("socios", month, kept, suppressed=omitted, tipos=tipos)


def build_pipeline(
    month: str,
    gold: dict,
    bronze: dict | None,
    silver: dict | None,
    quality: dict | None,
) -> dict:
    """Contagens e tempos dos manifestos. Nao copia data/hora, maquina nem caminhos."""
    checks = gold["checks"]
    data: dict[str, Any] = {
        "gold": {
            "modelos": [
                {
                    "nome": name,
                    "linhas": m["rows"],
                    "bytes_parquet": m["parquet_bytes"],
                    "segundos": m["seconds"],
                }
                for name, m in sorted(gold["models"].items())
            ],
            "reconciliacoes": [
                {
                    "nome": c["name"],
                    "descricao": c["description"],
                    "ok": c["ok"],
                    "bloqueante": c["blocking"],
                    "detalhe": {k: v for k, v in sorted(c["detail"].items()) if isinstance(v, int)},
                }
                for c in checks
            ],
        }
    }
    if bronze is not None:
        per_table: dict[str, dict[str, int]] = {}
        for f in bronze["files"]:
            t = per_table.setdefault(f["table"], {"linhas": 0, "bytes_csv": 0, "bytes_parquet": 0})
            t["linhas"] += f["rows"]
            t["bytes_csv"] += f["csv_bytes"]
            t["bytes_parquet"] += f["parquet_bytes"]
        data["bronze"] = {
            "linhas": bronze["total_rows"],
            "linhas_rejeitadas": bronze["total_rejected_lines"],
            "bytes_csv": bronze["total_csv_bytes"],
            "bytes_parquet": bronze["total_parquet_bytes"],
            "tabelas": [{"nome": n, **per_table[n]} for n in sorted(per_table)],
        }
    if silver is not None:
        data["silver"] = {
            "tabelas": [
                {
                    "nome": n,
                    "linhas_entrada": t["rows_in"],
                    "linhas_saida": t["rows_out"],
                    "chaves_invalidas": t["invalid_key_rows"],
                    "duplicadas_removidas": t["duplicate_rows_removed"],
                    "segundos": t["seconds"],
                }
                for n, t in sorted(silver["tables"].items())
            ]
        }
    if quality is not None:
        qchecks = quality["checks"]
        data["qualidade"] = {
            "verificacoes": len(qchecks),
            "verificacoes_bloqueantes": sum(1 for c in qchecks if c["blocking"]),
            "avisos": sum(1 for c in qchecks if c["status"] != "ok"),
            "amostra": bool(quality["quick"]),
            "segundos": quality["total_seconds"],
            "memoria_pico_mib": quality["peak_memory_mib"],
            "limite_memoria": quality["memory_limit"],
            "etapas": dict(sorted(quality["seconds_by_step"].items())),
        }
    return envelope("pipeline", month, data)


# --------------------------------------------------------------------------- #
# Orquestracao
# --------------------------------------------------------------------------- #


BUILDERS = {
    "overview": build_overview,
    "uf": build_uf,
    "aberturas_ano": build_aberturas_ano,
    "aberturas_mes": build_aberturas_mes,
    "aberturas_secao_ano": build_aberturas_secao_ano,
    "aberturas_uf_ano": build_aberturas_uf_ano,
    "ativos_secao": build_ativos_secao,
    "municipios_top": build_municipios_top,
    "perfil_porte": build_perfil_porte,
    "perfil_natureza": build_perfil_natureza,
    "capital_mediano": build_capital_mediano,
    "socios": build_socios,
}


def build_documents(
    gold_dir: Path, month: str, *, min_cell: int = MIN_CELL, quality_dir: Path | None = None
) -> dict[str, dict]:
    """Monta todos os documentos em memoria. Nada e gravado aqui."""
    if min_cell < 1:
        raise ExportError("min_cell deve ser pelo menos 1")
    gold = check_gold(read_manifest(gold_dir, month), month)
    base = gold_dir.parent
    bronze = read_manifest(base / "bronze", month)
    silver = read_manifest(base / "silver", month)
    quality = read_manifest(quality_dir if quality_dir is not None else base / "quality", month)
    con = _connect(gold_dir, month)
    try:
        documents = {name: builder(con, month, min_cell) for name, builder in BUILDERS.items()}
    finally:
        con.close()
    documents["pipeline"] = build_pipeline(month, gold, bronze, silver, quality)
    for name, doc in documents.items():
        extra = collect_keys(doc) - ALLOWED_KEYS
        if extra:
            raise ExportError(f"{name}: chaves fora da lista permitida: {sorted(extra)}")
        check_dynamic(name, doc)
    return documents


def render(documents: dict[str, dict], month: str) -> dict[str, str]:
    """Texto de cada arquivo, mais o indice do mes. Os tamanhos sao verificados aqui."""
    texts = {f"{name}.json": dumps(doc) for name, doc in documents.items()}
    for filename, text in texts.items():
        size = len(text.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise ExportError(f"{filename} tem {size} bytes, acima do limite de {MAX_FILE_BYTES}")
    listing = [
        {
            "arquivo": filename,
            "titulo": documents[filename.removesuffix(".json")]["titulo"],
            "bytes": len(text.encode("utf-8")),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        for filename, text in sorted(texts.items())
    ]
    index = {
        "esquema": SCHEMA_VERSION,
        "mes_referencia": month,
        "fonte": "Receita Federal do Brasil, dados abertos do CNPJ",
        "arquivos": listing,
    }
    texts["index.json"] = dumps(index)
    return texts


def write_month(texts: dict[str, str], out_dir: Path, month: str) -> Path:
    target = out_dir / month
    target.mkdir(parents=True, exist_ok=True)
    for stale in target.glob("*.json"):
        if stale.name not in texts:
            stale.unlink()
    for filename, text in texts.items():
        (target / filename).write_text(text, encoding="utf-8", newline="\n")
    write_months_index(out_dir)
    return target


def write_months_index(out_dir: Path) -> Path:
    months = sorted(
        p.name
        for p in out_dir.iterdir()
        if p.is_dir() and MONTH_RE.match(p.name) and (p / "index.json").exists()
    )
    doc = {"esquema": SCHEMA_VERSION, "meses": months, "ultimo": months[-1] if months else None}
    path = out_dir / "months.json"
    path.write_text(dumps(doc), encoding="utf-8", newline="\n")
    return path


def run(
    month: str,
    *,
    gold_dir: Path,
    out_dir: Path,
    min_cell: int = MIN_CELL,
    quality_dir: Path | None = None,
) -> Path:
    documents = build_documents(gold_dir, month, min_cell=min_cell, quality_dir=quality_dir)
    texts = render(documents, month)
    target = write_month(texts, out_dir, month)
    log.info("exportados %d arquivos para %s", len(texts), target)
    return target


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Exporta agregados da gold para JSON (dados do site).")
    parser.add_argument("--month", required=True, help="Mes de referencia no formato AAAA-MM")
    parser.add_argument("--gold-dir", type=Path, default=config.GOLD_DIR)
    parser.add_argument("--out", type=Path, default=Path("site/public/data"))
    parser.add_argument(
        "--min-cell", type=int, default=MIN_CELL, help="Contagem minima de um recorte para ser publicado"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    if not MONTH_RE.match(args.month):
        print(f"--month invalido: {args.month!r}. Use AAAA-MM, por exemplo 2026-10.", file=sys.stderr)
        return 2
    try:
        target = run(args.month, gold_dir=args.gold_dir, out_dir=args.out, min_cell=args.min_cell)
    except ExportError as exc:
        print(f"Exportacao recusada: {exc}", file=sys.stderr)
        return 1
    print(f"Arquivos gravados em {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
