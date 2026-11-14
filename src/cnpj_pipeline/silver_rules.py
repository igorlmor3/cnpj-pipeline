"""Regras de limpeza e tipagem da camada silver, por tabela e coluna.

TODAS AS REGRAS DESTE ARQUIVO SAO PROVISORIAS. Elas partem do layout historico e de
hipoteses sobre os dados, e devem ser confirmadas ou corrigidas depois do profiling do
mes real (docs/profiling/). Cada mudanca de regra deve ser registrada em
docs/data_quality_rules.md.

Cada coluna recebe um "tipo de regra" (kind):

  text    texto: troca caracteres de controle (inclusive os C1, U+0080 a U+009F) por espaco,
          junta espacos repetidos, apara as bordas e transforma vazio em NULL
  text_ph como text, mas valores-espaco-reservado (somente zeros, somente pontos ou hifens, N/A,
          NULL, NAO INFORMADO, SEM INFORMACAO) viram NULL
  code    codigo textual (zeros a esquerda importam): apenas apara as bordas, vazio vira NULL
  zero_null como code, mas valor composto somente por zeros (telefone, DDD, fax, CEP) vira NULL
  email   como lower, mas valores-espaco-reservado viram NULL
  upper   como code, mas em maiusculas
  lower   como code, mas em minusculas (e-mail)
  uf      sigla de UF valida (27 UFs e EX), em maiusculas; qualquer outro valor vira NULL
  date    data AAAAMMDD vira DATE; zeros e datas inexistentes viram NULL
  decimal decimal com virgula (ou ponto) vira DECIMAL(20,2); valor invalido vira NULL
  flag    S/N vira BOOLEAN; qualquer outro valor vira NULL
  list    lista separada por virgula vira VARCHAR[] (itens vazios e espacos nas bordas sao descartados)
  key8    chave de 8 posicoes (digitos e letras maiusculas, pelo CNPJ alfanumerico)
  key4    chave de 4 posicoes (ordem do estabelecimento)
  dv2     digitos verificadores (2 digitos)

Linhas cuja chave obrigatoria fica invalida (NULL apos a limpeza) vao para a quarentena.
"""

from __future__ import annotations

UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO", "EX",
)  # fmt: skip

_UF_LIST = ", ".join(f"'{uf}'" for uf in UFS)

# Limpeza base de texto: controles ASCII e C1 (U+0080..U+009F) viram espaco, espacos repetidos
# sao juntados, bordas aparadas e vazio vira NULL.
_TEXT_CLEAN = (
    "NULLIF(trim(regexp_replace(regexp_replace({c}, '[[:cntrl:]\\x{80}-\\x{9f}]', ' ', 'g'), "
    "' {2,}', ' ', 'g')), '')"
)

# Valores que significam "nao informado" mesmo sem serem NULL. Medidos no profiling de 2026-10
# (docs/profiling/2026-10/estabelecimentos.md): somente zeros, somente pontos ou hifens e rotulos.
_PLACEHOLDER_WORDS = (
    "'N/A', 'NULL', 'NAO INFORMADO', 'N\u00c3O INFORMADO', 'SEM INFORMACAO', 'SEM INFORMA\u00c7\u00c3O'"
)


def _null_if_placeholder(value_sql: str) -> str:
    test = f"upper({value_sql}) IN ({_PLACEHOLDER_WORDS}) OR regexp_matches({value_sql}, '^(0+|[.-]+)$')"
    return f"CASE WHEN {test} THEN NULL ELSE {value_sql} END"


# kind -> (tipo DuckDB, expressao SQL com o marcador {c} para a coluna de origem)
KINDS: dict[str, tuple[str, str]] = {
    "text": ("VARCHAR", _TEXT_CLEAN),
    "text_ph": ("VARCHAR", _null_if_placeholder(_TEXT_CLEAN)),
    "code": ("VARCHAR", "NULLIF(trim({c}), '')"),
    "upper": ("VARCHAR", "NULLIF(upper(trim({c})), '')"),
    "lower": ("VARCHAR", "NULLIF(lower(trim({c})), '')"),
    "email": ("VARCHAR", _null_if_placeholder("NULLIF(lower(trim({c})), '')")),
    "zero_null": (
        "VARCHAR",
        "CASE WHEN regexp_matches(trim({c}), '^0+$') THEN NULL ELSE NULLIF(trim({c}), '') END",
    ),
    "uf": ("VARCHAR", f"CASE WHEN upper(trim({{c}})) IN ({_UF_LIST}) THEN upper(trim({{c}})) END"),
    "date": (
        "DATE",
        "CASE WHEN regexp_matches(trim({c}), '^[0-9]{8}$') "
        "THEN CAST(try_strptime(trim({c}), '%Y%m%d') AS DATE) END",
    ),
    "decimal": ("DECIMAL(20,2)", "try_cast(replace(trim({c}), ',', '.') AS DECIMAL(20,2))"),
    "flag": ("BOOLEAN", "CASE upper(trim({c})) WHEN 'S' THEN TRUE WHEN 'N' THEN FALSE END"),
    # Sem lambdas: a sintaxe "->" foi descontinuada no DuckDB e "lambda x:" nao existe em versoes antigas.
    "list": (
        "VARCHAR[]",
        "CASE WHEN NULLIF(regexp_replace(trim({c}), '^[,[:space:]]+|[,[:space:]]+$', '', 'g'), '') IS NULL "
        "THEN NULL "
        "ELSE regexp_split_to_array(regexp_replace(trim({c}), '^[,[:space:]]+|[,[:space:]]+$', '', 'g'), "
        "'[[:space:]]*,[,[:space:]]*') END",
    ),
    "key8": (
        "VARCHAR",
        "CASE WHEN regexp_matches(upper(trim({c})), '^[0-9A-Z]{8}$') THEN upper(trim({c})) END",
    ),
    "key4": (
        "VARCHAR",
        "CASE WHEN regexp_matches(upper(trim({c})), '^[0-9A-Z]{4}$') THEN upper(trim({c})) END",
    ),
    "dv2": (
        "VARCHAR",
        "CASE WHEN regexp_matches(trim({c}), '^[0-9]{2}$') THEN trim({c}) END",
    ),
}

# Tipos em que "valor alterado" faz sentido como metrica (a saida continua sendo texto).
TEXT_LIKE = frozenset(
    {"text", "text_ph", "code", "zero_null", "upper", "lower", "email", "uf", "key8", "key4", "dv2"}
)

_DOMAIN_RULES = {"codigo": "code", "descricao": "text"}

RULES: dict[str, dict[str, str]] = {
    "Empresas": {
        "cnpj_basico": "key8",
        "razao_social": "text",
        "natureza_juridica": "code",
        "qualificacao_responsavel": "code",
        "capital_social": "decimal",
        "porte_empresa": "code",
        "ente_federativo_responsavel": "text",
    },
    "Estabelecimentos": {
        "cnpj_basico": "key8",
        "cnpj_ordem": "key4",
        "cnpj_dv": "dv2",
        "identificador_matriz_filial": "code",
        "nome_fantasia": "text_ph",
        "situacao_cadastral": "code",
        "data_situacao_cadastral": "date",
        "motivo_situacao_cadastral": "code",
        "nome_cidade_exterior": "text",
        "pais": "code",
        "data_inicio_atividade": "date",
        "cnae_fiscal_principal": "code",
        "cnae_fiscal_secundaria": "list",
        "tipo_logradouro": "text",
        "logradouro": "text_ph",
        "numero": "text_ph",
        "complemento": "text_ph",
        "bairro": "text_ph",
        "cep": "zero_null",
        "uf": "uf",
        "municipio": "code",
        "ddd_1": "zero_null",
        "telefone_1": "zero_null",
        "ddd_2": "zero_null",
        "telefone_2": "zero_null",
        "ddd_fax": "zero_null",
        "fax": "zero_null",
        "correio_eletronico": "email",
        "situacao_especial": "text",
        "data_situacao_especial": "date",
    },
    "Socios": {
        "cnpj_basico": "key8",
        "identificador_socio": "code",
        "nome_socio": "text",
        "cnpj_cpf_socio": "code",
        "qualificacao_socio": "code",
        "data_entrada_sociedade": "date",
        "pais": "code",
        "representante_legal": "code",
        "nome_representante": "text",
        "qualificacao_representante_legal": "code",
        "faixa_etaria": "code",
    },
    "Simples": {
        "cnpj_basico": "key8",
        "opcao_pelo_simples": "flag",
        "data_opcao_simples": "date",
        "data_exclusao_simples": "date",
        "opcao_pelo_mei": "flag",
        "data_opcao_mei": "date",
        "data_exclusao_mei": "date",
    },
    "Cnaes": _DOMAIN_RULES,
    "Motivos": _DOMAIN_RULES,
    "Municipios": _DOMAIN_RULES,
    "Naturezas": _DOMAIN_RULES,
    "Paises": _DOMAIN_RULES,
    "Qualificacoes": _DOMAIN_RULES,
}

# Colunas cuja chave limpa nao pode ser NULL; caso contrario a linha vai para a quarentena.
REQUIRED: dict[str, tuple[str, ...]] = {
    "Empresas": ("cnpj_basico",),
    "Estabelecimentos": ("cnpj_basico", "cnpj_ordem", "cnpj_dv"),
    "Socios": ("cnpj_basico",),
    "Simples": ("cnpj_basico",),
    **{t: ("codigo",) for t in ("Cnaes", "Motivos", "Municipios", "Naturezas", "Paises", "Qualificacoes")},
}

# Chave de deduplicacao. Socios nao tem chave unica, entao nao e deduplicada.
DEDUP_KEYS: dict[str, tuple[str, ...]] = {
    "Empresas": ("cnpj_basico",),
    "Estabelecimentos": ("cnpj_basico", "cnpj_ordem", "cnpj_dv"),
    "Simples": ("cnpj_basico",),
    **{t: ("codigo",) for t in ("Cnaes", "Motivos", "Municipios", "Naturezas", "Paises", "Qualificacoes")},
}


def clean_expr(kind: str, column_sql: str) -> str:
    """Expressao SQL de limpeza de uma coluna (column_sql ja qualificada e entre aspas)."""
    return KINDS[kind][1].replace("{c}", column_sql)


def output_type(kind: str) -> str:
    return KINDS[kind][0]
