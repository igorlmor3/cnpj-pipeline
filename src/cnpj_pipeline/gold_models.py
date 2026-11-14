"""Definicao dos modelos da camada gold: dimensoes, fato e marts, escritos em SQL do DuckDB.

Cada modelo le a silver (views `silver_<tabela>`) e/ou outros modelos da gold (views
`gold_<modelo>`) e grava um Parquet em data/gold/<modelo>/reference_month=<mes>/<modelo>.parquet.

Decisoes de modelagem (registradas em docs/architecture.md):
- grao do fato: um estabelecimento (CNPJ de 14 posicoes);
- o fato nao carrega dados de contato, endereco, nomes nem razao social: a gold e a base das saidas
  publicas (site e agente), que usam apenas agregados;
- "empresa ativa" nos marts e a matriz com situacao cadastral ativa (uma linha por empresa);
- empresa sem linha em Simples nao optou pelo Simples nem pelo MEI;
- os rotulos de codigos que a Receita documenta no layout (situacao cadastral, porte, tipo de socio,
  grupo de natureza juridica) e as secoes da CNAE (IBGE) sao tabelas fixas deste arquivo.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# Secoes da CNAE (IBGE): letra, descricao, divisao inicial e final.
CNAE_SECOES: tuple[tuple[str, str, int, int], ...] = (
    ("A", "Agricultura, pecuária, produção florestal, pesca e aquicultura", 1, 3),
    ("B", "Indústrias extrativas", 5, 9),
    ("C", "Indústrias de transformação", 10, 33),
    ("D", "Eletricidade e gás", 35, 35),
    ("E", "Água, esgoto, atividades de gestão de resíduos e descontaminação", 36, 39),
    ("F", "Construção", 41, 43),
    ("G", "Comércio; reparação de veículos automotores e motocicletas", 45, 47),
    ("H", "Transporte, armazenagem e correio", 49, 53),
    ("I", "Alojamento e alimentação", 55, 56),
    ("J", "Informação e comunicação", 58, 63),
    ("K", "Atividades financeiras, de seguros e serviços relacionados", 64, 66),
    ("L", "Atividades imobiliárias", 68, 68),
    ("M", "Atividades profissionais, científicas e técnicas", 69, 75),
    ("N", "Atividades administrativas e serviços complementares", 77, 82),
    ("O", "Administração pública, defesa e seguridade social", 84, 84),
    ("P", "Educação", 85, 85),
    ("Q", "Saúde humana e serviços sociais", 86, 88),
    ("R", "Artes, cultura, esporte e recreação", 90, 93),
    ("S", "Outras atividades de serviços", 94, 96),
    ("T", "Serviços domésticos", 97, 97),
    ("U", "Organismos internacionais e outras instituições extraterritoriais", 99, 99),
)

# Layout da Receita: situacao cadastral do estabelecimento.
SITUACOES_CADASTRAIS: tuple[tuple[str, str], ...] = (
    ("01", "Nula"),
    ("02", "Ativa"),
    ("03", "Suspensa"),
    ("04", "Inapta"),
    ("08", "Baixada"),
)
SITUACAO_ATIVA = "02"

# Layout da Receita: porte da empresa.
PORTES: tuple[tuple[str, str], ...] = (
    ("00", "Não informado"),
    ("01", "Microempresa"),
    ("03", "Empresa de pequeno porte"),
    ("05", "Demais"),
)

# Grupo da natureza juridica: primeiro digito do codigo (tabela de natureza juridica da CONCLA).
NATUREZA_GRUPOS: tuple[tuple[str, str], ...] = (
    ("1", "Administração Pública"),
    ("2", "Entidades Empresariais"),
    ("3", "Entidades sem Fins Lucrativos"),
    ("4", "Pessoas Físicas"),
    ("5", "Organizações Internacionais e Outras Instituições Extraterritoriais"),
)

# Layout da Receita: identificador de socio.
TIPOS_SOCIO: tuple[tuple[str, str], ...] = (
    ("1", "Pessoa jurídica"),
    ("2", "Pessoa física"),
    ("3", "Estrangeiro"),
)

MATRIZ = "1"  # identificador_matriz_filial: 1 = matriz, 2 = filial


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def values_sql(rows: tuple[tuple, ...], columns: tuple[str, ...], alias: str = "t") -> str:
    """Tabela fixa em linha: (VALUES (...), (...)) AS <alias>(col, ...)."""
    lines = ", ".join(
        "(" + ", ".join(_q(v) if isinstance(v, str) else str(v) for v in row) + ")" for row in rows
    )
    return f"(VALUES {lines}) AS {alias}({', '.join(columns)})"


def case_sql(value_sql: str, mapping: tuple[tuple[str, str], ...]) -> str:
    whens = " ".join(f"WHEN {_q(code)} THEN {_q(label)}" for code, label in mapping)
    return f"CASE {value_sql} {whens} END"


def secao_case(division_int_sql: str) -> str:
    """Letra da secao da CNAE a partir da divisao (inteiro de 2 digitos); NULL se nao houver."""
    whens = " ".join(
        f"WHEN {division_int_sql} BETWEEN {lo} AND {hi} THEN {_q(letter)}"
        for letter, _, lo, hi in CNAE_SECOES
    )
    return f"CASE {whens} END"


def meta_sql(month: str, processed_at: str) -> str:
    """Colunas de metadados presentes em todas as tabelas da gold."""
    return (
        f"CAST({_q(month)} AS VARCHAR) AS _reference_month, "
        f"CAST({_q(processed_at)} AS TIMESTAMP) AS _gold_processed_at"
    )


# --------------------------------------------------------------------------- #
# Dimensoes
# --------------------------------------------------------------------------- #


def dim_cnae(month: str, ts: str) -> str:
    division = "try_cast(substr(s.codigo, 1, 2) AS INTEGER)"
    return f"""
        SELECT s.codigo, s.descricao, substr(s.codigo, 1, 2) AS divisao,
               {secao_case(division)} AS secao, {meta_sql(month, ts)}
        FROM silver_cnaes s
        ORDER BY s.codigo
    """


def dim_cnae_secao(month: str, ts: str) -> str:
    sections = values_sql(CNAE_SECOES, ("codigo", "descricao", "divisao_inicial", "divisao_final"))
    return f"""
        SELECT t.codigo, t.descricao, CAST(t.divisao_inicial AS INTEGER) AS divisao_inicial,
               CAST(t.divisao_final AS INTEGER) AS divisao_final, {meta_sql(month, ts)}
        FROM {sections}
        ORDER BY t.codigo
    """


def dim_natureza_juridica(month: str, ts: str) -> str:
    groups = values_sql(NATUREZA_GRUPOS, ("grupo", "grupo_descricao"), alias="g")
    return f"""
        SELECT s.codigo, s.descricao, substr(s.codigo, 1, 1) AS grupo, g.grupo_descricao,
               {meta_sql(month, ts)}
        FROM silver_naturezas s
        LEFT JOIN {groups} ON g.grupo = substr(s.codigo, 1, 1)
        ORDER BY s.codigo
    """


def dim_municipio(month: str, ts: str) -> str:
    return f"""
        SELECT s.codigo, s.descricao, {meta_sql(month, ts)}
        FROM silver_municipios s
        ORDER BY s.codigo
    """


def dim_qualificacao_socio(month: str, ts: str) -> str:
    return f"""
        SELECT s.codigo, s.descricao, {meta_sql(month, ts)}
        FROM silver_qualificacoes s
        ORDER BY s.codigo
    """


def dim_situacao_cadastral(month: str, ts: str) -> str:
    rows = values_sql(SITUACOES_CADASTRAIS, ("codigo", "descricao"))
    return f"SELECT t.codigo, t.descricao, {meta_sql(month, ts)} FROM {rows} ORDER BY t.codigo"


def dim_porte(month: str, ts: str) -> str:
    rows = values_sql(PORTES, ("codigo", "descricao"))
    return f"SELECT t.codigo, t.descricao, {meta_sql(month, ts)} FROM {rows} ORDER BY t.codigo"


# --------------------------------------------------------------------------- #
# Fato
# --------------------------------------------------------------------------- #


def fato_estabelecimento(month: str, ts: str) -> str:
    return f"""
        SELECT
            b.cnpj,
            b.cnpj_basico,
            b.cnpj_ordem,
            (b.identificador_matriz_filial = {_q(MATRIZ)}) AS is_matriz,
            b.situacao_cadastral,
            (b.situacao_cadastral = {_q(SITUACAO_ATIVA)}) AS is_ativo,
            b.data_situacao_cadastral,
            b.motivo_situacao_cadastral,
            b.data_inicio_atividade,
            CAST(year(b.data_inicio_atividade) AS INTEGER) AS ano_inicio_atividade,
            strftime(b.data_inicio_atividade, '%Y-%m') AS ano_mes_inicio_atividade,
            b.cnae_fiscal_principal AS cnae_principal,
            substr(b.cnae_fiscal_principal, 1, 2) AS cnae_divisao,
            {secao_case("b._div")} AS cnae_secao,
            b.uf,
            b.municipio,
            p.natureza_juridica,
            p.porte_empresa,
            p.capital_social,
            coalesce(sm.opcao_pelo_simples, FALSE) AS is_optante_simples,
            coalesce(sm.opcao_pelo_mei, FALSE) AS is_mei,
            {meta_sql(month, ts)}
        FROM (
            SELECT e.*, try_cast(substr(e.cnae_fiscal_principal, 1, 2) AS INTEGER) AS _div
            FROM silver_estabelecimentos e
        ) b
        LEFT JOIN silver_empresas p ON p.cnpj_basico = b.cnpj_basico
        LEFT JOIN silver_simples sm ON sm.cnpj_basico = b.cnpj_basico
    """


# --------------------------------------------------------------------------- #
# Marts
# --------------------------------------------------------------------------- #


def mart_aberturas_mensal(month: str, ts: str) -> str:
    return f"""
        SELECT
            f.ano_mes_inicio_atividade AS ano_mes,
            f.ano_inicio_atividade AS ano,
            f.uf,
            f.cnae_secao,
            count(*) AS estabelecimentos_abertos,
            count(*) FILTER (WHERE f.is_matriz) AS matrizes_abertas,
            count(*) FILTER (WHERE f.is_ativo) AS ainda_ativos,
            {meta_sql(month, ts)}
        FROM gold_fato_estabelecimento f
        WHERE f.data_inicio_atividade IS NOT NULL
        GROUP BY f.ano_mes_inicio_atividade, f.ano_inicio_atividade, f.uf, f.cnae_secao
        ORDER BY ano_mes, f.uf, f.cnae_secao
    """


def mart_estabelecimentos_ativos(month: str, ts: str) -> str:
    return f"""
        SELECT
            f.uf,
            f.municipio,
            m.descricao AS municipio_nome,
            f.cnae_divisao,
            f.cnae_secao,
            s.descricao AS cnae_secao_descricao,
            count(*) AS estabelecimentos_ativos,
            count(*) FILTER (WHERE f.is_matriz) AS matrizes_ativas,
            count(*) FILTER (WHERE f.is_optante_simples) AS optantes_simples,
            count(*) FILTER (WHERE f.is_mei) AS optantes_mei,
            {meta_sql(month, ts)}
        FROM gold_fato_estabelecimento f
        LEFT JOIN gold_dim_municipio m ON m.codigo = f.municipio
        LEFT JOIN gold_dim_cnae_secao s ON s.codigo = f.cnae_secao
        WHERE f.is_ativo
        GROUP BY f.uf, f.municipio, m.descricao, f.cnae_divisao, f.cnae_secao, s.descricao
        ORDER BY f.uf, f.municipio, f.cnae_divisao
    """


def mart_perfil_porte_natureza(month: str, ts: str) -> str:
    return f"""
        SELECT
            f.porte_empresa,
            p.descricao AS porte_descricao,
            f.natureza_juridica,
            n.descricao AS natureza_descricao,
            n.grupo_descricao AS natureza_grupo,
            count(*) AS empresas_ativas,
            sum(f.capital_social) AS capital_social_total,
            CAST(median(f.capital_social) AS DECIMAL(20,2)) AS capital_social_mediano,
            {meta_sql(month, ts)}
        FROM gold_fato_estabelecimento f
        LEFT JOIN gold_dim_porte p ON p.codigo = f.porte_empresa
        LEFT JOIN gold_dim_natureza_juridica n ON n.codigo = f.natureza_juridica
        WHERE f.is_matriz AND f.is_ativo
        GROUP BY f.porte_empresa, p.descricao, f.natureza_juridica, n.descricao, n.grupo_descricao
        ORDER BY f.porte_empresa, f.natureza_juridica
    """


def mart_simples_mei_uf(month: str, ts: str) -> str:
    return f"""
        SELECT
            f.uf,
            count(*) AS empresas_ativas,
            count(*) FILTER (WHERE f.is_optante_simples) AS optantes_simples,
            count(*) FILTER (WHERE f.is_mei) AS optantes_mei,
            round(100.0 * count(*) FILTER (WHERE f.is_optante_simples) / count(*), 2) AS pct_simples,
            round(100.0 * count(*) FILTER (WHERE f.is_mei) / count(*), 2) AS pct_mei,
            {meta_sql(month, ts)}
        FROM gold_fato_estabelecimento f
        WHERE f.is_matriz AND f.is_ativo
        GROUP BY f.uf
        ORDER BY f.uf
    """


def mart_socios_qualificacao(month: str, ts: str) -> str:
    tipos = values_sql(TIPOS_SOCIO, ("codigo", "descricao"), alias="tp")
    return f"""
        SELECT
            s.qualificacao_socio,
            q.descricao AS qualificacao_descricao,
            s.identificador_socio,
            tp.descricao AS tipo_socio,
            count(*) AS socios,
            {meta_sql(month, ts)}
        FROM silver_socios s
        LEFT JOIN gold_dim_qualificacao_socio q ON q.codigo = s.qualificacao_socio
        LEFT JOIN {tipos} ON tp.codigo = s.identificador_socio
        GROUP BY s.qualificacao_socio, q.descricao, s.identificador_socio, tp.descricao
        ORDER BY s.qualificacao_socio, s.identificador_socio
    """


# --------------------------------------------------------------------------- #
# Registro dos modelos (em ordem de dependencia)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Model:
    name: str
    kind: str  # "dim", "fato" ou "mart"
    sources: tuple[str, ...]  # tabelas da silver (nomes como em config.ALL_TABLES)
    depends: tuple[str, ...]  # modelos da gold
    sql: Callable[[str, str], str]


MODELS: tuple[Model, ...] = (
    Model("dim_cnae", "dim", ("Cnaes",), (), dim_cnae),
    Model("dim_cnae_secao", "dim", (), (), dim_cnae_secao),
    Model("dim_natureza_juridica", "dim", ("Naturezas",), (), dim_natureza_juridica),
    Model("dim_municipio", "dim", ("Municipios",), (), dim_municipio),
    Model("dim_qualificacao_socio", "dim", ("Qualificacoes",), (), dim_qualificacao_socio),
    Model("dim_situacao_cadastral", "dim", (), (), dim_situacao_cadastral),
    Model("dim_porte", "dim", (), (), dim_porte),
    Model(
        "fato_estabelecimento",
        "fato",
        ("Estabelecimentos", "Empresas", "Simples"),
        (),
        fato_estabelecimento,
    ),
    Model("mart_aberturas_mensal", "mart", (), ("fato_estabelecimento",), mart_aberturas_mensal),
    Model(
        "mart_estabelecimentos_ativos",
        "mart",
        (),
        ("fato_estabelecimento", "dim_municipio", "dim_cnae_secao"),
        mart_estabelecimentos_ativos,
    ),
    Model(
        "mart_perfil_porte_natureza",
        "mart",
        (),
        ("fato_estabelecimento", "dim_porte", "dim_natureza_juridica"),
        mart_perfil_porte_natureza,
    ),
    Model("mart_simples_mei_uf", "mart", (), ("fato_estabelecimento",), mart_simples_mei_uf),
    Model(
        "mart_socios_qualificacao",
        "mart",
        ("Socios",),
        ("dim_qualificacao_socio",),
        mart_socios_qualificacao,
    ),
)

MODEL_NAMES = tuple(m.name for m in MODELS)
MODELS_BY_NAME = {m.name: m for m in MODELS}
