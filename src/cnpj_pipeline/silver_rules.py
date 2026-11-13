"""Regras de limpeza e tipagem da camada silver, por tabela e coluna.

TODAS AS REGRAS DESTE ARQUIVO SAO PROVISORIAS. Elas partem do layout historico e de
hipoteses sobre os dados, e devem ser confirmadas ou corrigidas depois do profiling do
mes real (docs/profiling/). Cada mudanca de regra deve ser registrada em
docs/data_quality_rules.md.

Cada coluna recebe um "tipo de regra" (kind):

  text    texto: troca caracteres de controle por espaco, junta espacos repetidos, apara as
          bordas e transforma vazio em NULL
  code    codigo textual (zeros a esquerda importam): apenas apara as bordas, vazio vira NULL
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

# kind -> (tipo DuckDB, expressao SQL com o marcador {c} para a coluna de origem)
KINDS: dict[str, tuple[str, str]] = {
    "text": (
        "VARCHAR",
        "NULLIF(trim(regexp_replace(regexp_replace({c}, '[[:cntrl:]]', ' ', 'g'), ' {2,}', ' ', 'g')), '')",
    ),
    "code": ("VARCHAR", "NULLIF(trim({c}), '')"),
    "upper": ("VARCHAR", "NULLIF(upper(trim({c})), '')"),
    "lower": ("VARCHAR", "NULLIF(lower(trim({c})), '')"),
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
TEXT_LIKE = frozenset({"text", "code", "upper", "lower", "uf", "key8", "key4", "dv2"})

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
        "nome_fantasia": "text",
        "situacao_cadastral": "code",
        "data_situacao_cadastral": "date",
        "motivo_situacao_cadastral": "code",
        "nome_cidade_exterior": "text",
        "pais": "code",
        "data_inicio_atividade": "date",
        "cnae_fiscal_principal": "code",
        "cnae_fiscal_secundaria": "list",
        "tipo_logradouro": "text",
        "logradouro": "text",
        "numero": "text",
        "complemento": "text",
        "bairro": "text",
        "cep": "code",
        "uf": "uf",
        "municipio": "code",
        "ddd_1": "code",
        "telefone_1": "code",
        "ddd_2": "code",
        "telefone_2": "code",
        "ddd_fax": "code",
        "fax": "code",
        "correio_eletronico": "lower",
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
